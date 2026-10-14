#!/usr/bin/env bash
# Publish the launch DB to production: upload a dump, run deploy.yml with it, wait.
#
#     backend/scripts/publish_snapshot.sh
#
# Every publish from this machine goes through here, by hand or from a script, so they
# never overlap: it holds a machine-wide lock (PUBLISH_LOCK, default
# ~/.local/state/schooldecider/publish.lock) from the upload until the run ends. Hold the
# same lock around launch-DB writes that must land in one snapshot, and tell this script
# you hold it:
#
#     flock "$(backend/scripts/publish_snapshot.sh --lock-path)" sh -c \
#         '<write> && PUBLISH_LOCK_HELD=1 backend/scripts/publish_snapshot.sh'
#
# The lock is released only once the deploy run has finished (or after two hours).
# Prints the dump name, the run URL and its summary; exits non-zero if the run fails.
# Check the live API afterwards (skills/common-tasks/SKILL.md, Publish data to production).
set -euo pipefail

lock="${PUBLISH_LOCK:-$HOME/.local/state/schooldecider/publish.lock}"
if [ "${1:-}" = "--lock-path" ]; then
	mkdir -p "$(dirname "$lock")"
	echo "$lock"
	exit 0
fi
die() {
	echo "publish: $*" >&2
	exit 1
}
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repo="${PUBLISH_REPO:-mike2906/school-comparison}"

mkdir -p "$(dirname "$lock")"
exec 9>>"$lock"
# A caller already holding the lock (flock <lock> this script) passes PUBLISH_LOCK_HELD=1.
if [ -z "${PUBLISH_LOCK_HELD:-}" ]; then
	echo "publish: waiting for the publish lock" >&2
	flock -w 3600 9 || die "another publish held the lock for an hour"
fi

output="$("$repo_root/backend/scripts/backup_offsite.sh")" || die "backup_offsite.sh failed"
echo "$output"
dump="$(echo "$output" | sed -n 's/^backup: stored \([^ ]*\.dump\) .*/\1/p' | tail -1)"
[ -n "$dump" ] || die "backup_offsite.sh named no dump"

runs() {
	gh run list -R "$repo" --workflow deploy.yml --event workflow_dispatch --limit 20 --json databaseId -q '.[].databaseId'
}
before="$(runs)" || die "could not list deploy runs"
gh workflow run deploy.yml -R "$repo" --ref main -f snapshot="$dump" || die "could not start deploy.yml"
run_id=""
for _ in $(seq 12); do
	sleep 10
	# The newest dispatched run that was not there before ours (no clock comparison).
	run_id="$(runs | grep -vxF -f <(echo "$before") | head -1)" || true
	[ -n "$run_id" ] && break
done
[ -n "$run_id" ] || die "deploy.yml run for $dump did not appear"
echo "publish: $dump -> https://github.com/$repo/actions/runs/$run_id"
# Hold the lock until the run is really over: poll, and ride out gh or network errors.
conclusion=""
for _ in $(seq 240); do
	read -r status conclusion < <(gh run view "$run_id" -R "$repo" --json status,conclusion -q '"\(.status) \(.conclusion)"' 2>/dev/null || echo "unknown -")
	[ "$status" = completed ] && break
	sleep 30
done
gh run view "$run_id" -R "$repo" --log 2>/dev/null | grep -o 'deploy: published the snapshot.*' | tail -1 || true
[ "$conclusion" = success ] || die "deploy run $run_id ended ${conclusion:-without finishing in two hours}"
echo "publish: done"
