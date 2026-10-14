#!/usr/bin/env bash
# Import new admission cut-off rounds into the launch DB and publish them, unattended.
#
# Run hourly by cron on the machine with the launch DB (setup: docs/ADMISSION_CUTOFFS.md,
# Publishing new rounds). Does nothing until the cutoff-data branch moves; then, under
# the publish lock: dry run, checks, pg_dump backup, apply, publish_snapshot.sh, and a
# check that the live API shows as many schools with cut-offs as the launch DB.
#
# Stops without writing when more than CUTOFF_MAX_UNMATCHED (default 5) institutions are
# unmatched or the archive holds fewer rows than at the last publish (the archive only
# grows, so fewer rows means a capture bug), and opens one GitHub issue. A failed publish
# is retried, and a failed live check re-checked, by the next run. Log:
# $state/publish_cutoffs.log.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
archive="${CUTOFF_ARCHIVE:-$HOME/cutoff-archive}"
state="${CUTOFF_STATE_DIR:-$HOME/.local/state/schooldecider}"
backups="${CUTOFF_BACKUPS:-$HOME/backups}"
max_unmatched="${CUTOFF_MAX_UNMATCHED:-5}"
api="${CUTOFF_API:-https://api.schooldecider.com}"
repo="${PUBLISH_REPO:-mike2906/school-comparison}"
container="${BACKUP_DB_CONTAINER:-sofia_schools_db}"
database="${BACKUP_DATABASE:-sofia_schools}"

mkdir -p "$state" "$backups"
exec >> "$state/publish_cutoffs.log" 2>&1
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
stop() {
	log "STOPPED: $*"
	# One open issue says the publish needs a look; later stops only add to the log.
	title="Automatic cut-off publish stopped"
	if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$title\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
		gh issue create -R "$repo" --title "$title" --body "backend/scripts/publish_cutoffs.sh on $(hostname) stopped at $(date -u +%Y-%m-%dT%H:%M:%SZ): $*

New admission rounds are not reaching the site until this is fixed. Log: $state/publish_cutoffs.log. Close this issue once fixed." > /dev/null || true
	fi
	exit 1
}
field() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"; }

live_check() {
	local expected live
	expected="$(field "$state/cutoffs_applied.json" schools_with_summary)"
	live="$(curl -fsS "$api/schools?country_code=bg&city=sofia" | python3 -c '
import json, sys
print(sum(1 for s in json.load(sys.stdin) if (s.get("admission_info") or {}).get("cutoff_summary")))')" || live="?"
	[ "$live" = "$expected" ] || stop "live API shows $live schools with cut-offs, the launch DB $expected"
	cp "$state/cutoffs_applied.json" "$state/cutoffs_published.json"
	rm -f "$state/cutoffs_unverified"
	log "published: $live schools with cut-offs live"
}

git -C "$archive" fetch -q origin cutoff-data || stop "git fetch cutoff-data failed"
head="$(git -C "$archive" rev-parse origin/cutoff-data)"
[ "$head" != "$(cat "$state/cutoffs_published_sha" 2>/dev/null)" ] || exit 0

exec 9>>"$("$repo_root/backend/scripts/publish_snapshot.sh" --lock-path)"
flock -w 3600 9 || stop "the publish lock was held for an hour"
# A run that held the lock may have published this commit meanwhile.
[ "$head" != "$(cat "$state/cutoffs_published_sha" 2>/dev/null)" ] || exit 0
# Published but the live check failed last time: check again before anything new.
[ ! -f "$state/cutoffs_unverified" ] || live_check

git -C "$archive" checkout -q --detach "$head"
cd "$repo_root/backend"
uv run python scripts/import_cutoffs.py --archive "$archive" --summary-json "$state/cutoffs_dry_run.json" > /dev/null \
	|| stop "dry run failed"
# "unpublished" marks an apply whose publish failed: publish it even with nothing new.
if [ "$(field "$state/cutoffs_dry_run.json" schools_changed)" = 0 ] && [ ! -f "$state/cutoffs_unpublished" ]; then
	echo "$head" > "$state/cutoffs_published_sha"
	exit 0  # a capture with no new rounds
fi
log "cutoff-data $head: $(cat "$state/cutoffs_dry_run.json")"
unmatched="$(field "$state/cutoffs_dry_run.json" unmatched_institutions)"
[ "$unmatched" -le "$max_unmatched" ] || stop "$unmatched unmatched institutions (max $max_unmatched)"
rows="$(field "$state/cutoffs_dry_run.json" rows)"
if [ -f "$state/cutoffs_published.json" ]; then
	before="$(field "$state/cutoffs_published.json" rows)"
	[ "$rows" -ge "$before" ] || stop "the archive shrank from $before to $rows rows"
fi

backup="$backups/$database-pre-cutoffs-$(date -u +%Y%m%dT%H%M%SZ).dump"
if ! docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$backup" || ! [ -s "$backup" ]; then
	rm -f "$backup"
	stop "pg_dump failed"
fi
log "backup $backup"
uv run python scripts/import_cutoffs.py --archive "$archive" --apply --max-unmatched "$max_unmatched" \
	--summary-json "$state/cutoffs_applied.json" > /dev/null || stop "apply failed"
log "applied: $(cat "$state/cutoffs_applied.json")"
touch "$state/cutoffs_unpublished"

PUBLISH_LOCK_HELD=1 "$repo_root/backend/scripts/publish_snapshot.sh" || stop "publish failed (applied, not published; the next run retries)"
rm -f "$state/cutoffs_unpublished"
touch "$state/cutoffs_unverified"
live_check
echo "$head" > "$state/cutoffs_published_sha"
