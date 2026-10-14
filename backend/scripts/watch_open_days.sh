#!/usr/bin/env bash
# Check schools' pages for new open days and application dates, and publish what changed.
#
# Run hourly by cron on the machine with the launch DB (setup: docs/SCHOOL_FACTS.md, Open
# days). Does nothing until a check is due: a day after the last good one, or a week in
# July and August, when schools announce nothing. A laptop that slept through the hour
# catches up at the next one. Runs the `open-days` stage with --apply (no model call unless
# a mention changed; spend cap OPEN_DAYS_MAX_USD, default $0.30), then, only when some
# school's dates changed, publishes with publish_snapshot.sh under the publish lock.
#
# The stage writes one school at a time and each write stands on its own, so it runs
# outside the publish lock: a publish by another script meanwhile only takes some schools'
# new dates early. A failed run or publish opens one GitHub issue and is retried at the
# next hour. Log and reports: $state/open_days.log, $state/open-days-*.json; a pg_dump before
# each run (the last 7 kept) in $backups.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
state="${OPEN_DAYS_STATE_DIR:-$HOME/.local/state/schooldecider}"
max_usd="${OPEN_DAYS_MAX_USD:-0.30}"
day_usd="${OPEN_DAYS_DAY_USD:-0.60}"  # all runs on one day (UTC), retries included
city="${OPEN_DAYS_CITY:-sofia}"
repo="${PUBLISH_REPO:-mike2906/school-comparison}"
backups="${OPEN_DAYS_BACKUPS:-$HOME/backups}"
container="${BACKUP_DB_CONTAINER:-sofia_schools_db}"
database="${BACKUP_DATABASE:-sofia_schools}"

mkdir -p "$state" "$backups"
exec >> "$state/open_days.log" 2>&1
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
stop() {
	log "STOPPED: $*"
	title="Open-days check stopped"
	if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$title\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
		gh issue create -R "$repo" --title "$title" --body "backend/scripts/watch_open_days.sh on $(hostname) stopped at $(date -u +%Y-%m-%dT%H:%M:%SZ): $*

New open days are not reaching the site until this is fixed; the next hourly run tries again. Log: $state/open_days.log. Close this issue once fixed." > /dev/null || true
	fi
	exit 1
}

# One check at a time; a check still running when cron fires again is left to finish.
exec 8>>"$state/open_days.lock"
flock -n 8 || exit 0

# "unpublished" marks a run whose dates changed but whose publish failed: publish first.
if [ ! -f "$state/open_days_unpublished" ]; then
	month="$(date -u +%m)"
	due_hours=20
	[ "$month" != 07 ] && [ "$month" != 08 ] || due_hours=164
	last="$(cat "$state/open_days_last_ok" 2>/dev/null || echo 0)"
	[ $(($(date -u +%s) - last)) -ge $((due_hours * 3600)) ] || exit 0
	today="$(date -u +%F)"
	spent="$(awk -v d="$today" '$1 == d { s += $2 } END { printf "%.4f", s }' "$state/open_days_spend" 2>/dev/null || echo 0)"
	if ! python3 -c 'import sys; sys.exit(float(sys.argv[1]) + float(sys.argv[2]) > float(sys.argv[3]))' "$spent" "$max_usd" "$day_usd"; then
		log "skipped: \$$spent spent today, another run could pass the \$$day_usd day cap"
		exit 0
	fi

	backup="$backups/$database-pre-open-days-$(date -u +%Y%m%dT%H%M%SZ).dump"
	if ! docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$backup" || ! [ -s "$backup" ]; then
		rm -f "$backup"
		stop "pg_dump failed"
	fi
	# The last week of these backups is enough: each run changes only dates and page hashes.
	ls -1t "$backups"/"$database"-pre-open-days-*.dump | tail -n +8 | xargs -r rm -f

	report="$state/open-days-$(date -u +%Y%m%dT%H%M%SZ).json"
	cd "$repo_root/backend"
	# A small reserve per call: the reader and judge cost well under a cent, and the default
	# reserve times the workers in flight would stop the run at a low cap.
	if ! PROVIDER_REQUEST_RESERVE_USD="${PROVIDER_REQUEST_RESERVE_USD:-0.01}" \
		uv run python -m app.scrapers.cli open-days --city "$city" --apply --max-usd "$max_usd" --out "$report" > /dev/null; then
		echo "$today $max_usd" >> "$state/open_days_spend"  # unknown: count the whole cap
		stop "the open-days stage failed"
	fi
	python3 -c 'import json, sys; print(sys.argv[2], json.load(open(sys.argv[1]))["spent_usd"])' "$report" "$today" >> "$state/open_days_spend"
	summary="$(python3 -c '
import json, sys
r = json.load(open(sys.argv[1]))
print(json.dumps({k: r[k] for k in ("schools", "outcomes", "facts_changed", "spent_usd", "stopped")}))' "$report")"
	log "checked: $summary"
	changed="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["facts_changed"])' "$report")"
	stopped="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["stopped"] or "")' "$report")"
	# Old reports: keep a month.
	find "$state" -name 'open-days-*.json' -mtime +30 -delete
	[ "$changed" = 0 ] || touch "$state/open_days_unpublished"
	# A run cut short (spend cap) still publishes what it wrote; the schools it did not reach
	# wait for the next day's check rather than spending again this hour.
	case "$stopped" in
	"") ;;
	"run stopped"*) stop "$stopped" ;; # a crash, not a cap: what it wrote is published next hour
	*) log "run stopped early: $stopped" ;;
	esac
	date -u +%s > "$state/open_days_last_ok"
fi

if [ -f "$state/open_days_unpublished" ]; then
	"$repo_root/backend/scripts/publish_snapshot.sh" || stop "publish failed (dates stored, not published; the next run retries)"
	rm -f "$state/open_days_unpublished"
	log "published"
fi
