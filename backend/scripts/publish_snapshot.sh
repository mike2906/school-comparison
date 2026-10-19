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
# Before uploading, it compares the school list with the live one and stops if many
# schools would lose something they show now (cli publish-diff).
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

# Stop a publish that would take away what many schools show now (publish_diff.py), and
# say so in one rolling issue. When the live list can't be read (exit 75) the publish goes
# ahead, since it may be the fix; any other failure, such as a launch DB the list can't be
# built from, stops it. PUBLISH_ALLOW_DROPS=1 publishes an intended drop.
if [ -z "${PUBLISH_ALLOW_DROPS:-}" ]; then
	title="Publish stopped: schools would lose data"
	body="$(dirname "$lock")/publish-diff-issue.md"
	rm -f "$body"
	rc=0
	(cd "$repo_root/backend" && uv run python -m app.scrapers.cli publish-diff --issue-body "$body") || rc=$?
	if [ "$rc" = 3 ]; then
		# A failed lookup must not open a second issue; the next try raises it. Not --search:
		# its index lags, so a stop right after another would open a duplicate.
		if number="$(gh issue list -R "$repo" --state open --limit 200 --json number,title -q "map(select(.title == \"$title\"))[0].number // empty")"; then
			if [ -n "$number" ]; then
				gh issue edit "$number" -R "$repo" --body-file "$body" > /dev/null || true
			else
				gh issue create -R "$repo" --title "$title" --body-file "$body" > /dev/null || true
			fi
		fi
		die "stopped: many schools would lose data they show now (GitHub issue \"$title\")"
	elif [ "$rc" = 75 ]; then
		echo "publish: the live list could not be read; publishing without the diff" >&2
	elif [ "$rc" != 0 ]; then
		die "the publish diff failed (exit $rc); nothing published"
	fi
fi

output="$("$repo_root/backend/scripts/backup_offsite.sh")" || die "backup_offsite.sh failed"
echo "$output"
dump="$(echo "$output" | sed -n 's/^backup: stored \([^ ]*\.dump\) .*/\1/p' | tail -1)"
[ -n "$dump" ] || die "backup_offsite.sh named no dump"

runs() {
	gh run list -R "$repo" --workflow deploy.yml --event workflow_dispatch --limit 20 --json databaseId -q '.[].databaseId'
}
before="$(runs)" || die "could not list deploy runs"
started="$(gh workflow run deploy.yml -R "$repo" --ref main -f snapshot="$dump" 2>&1)" || die "could not start deploy.yml: $started"
# Newer gh prints the new run's URL; older gh prints nothing, so fall back to the newest
# dispatched run that was not there before ours.
run_id="$(echo "$started" | sed -n 's#.*/actions/runs/\([0-9][0-9]*\).*#\1#p' | head -1)"
for _ in $(seq 12); do
	[ -z "$run_id" ] || break
	sleep 10
	run_id="$(runs | grep -vxF -f <(echo "$before") | head -1)" || true
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
