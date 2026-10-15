#!/usr/bin/env bash
# Place hidden school locations on the map, weekly, and publish what was placed.
#
# Run hourly by cron on the machine with the launch DB (setup: skills/geocoding/SKILL.md,
# Weekly run). Does nothing until a run is due, a week after the last good one, so a
# laptop that slept catches up at the next hour. Runs `website-pins` and then `pin-agent`
# (about $0.02 for Sofia, cap PINS_MAX_USD) over the locations still hidden: new schools,
# changed addresses, and schools OSM or their website now places.
#
# Each stage first runs dry: more than PINS_MAX_PLACED (default 10) placements in one week
# means a bug or a source gone wrong, so the script stops before writing. Otherwise it
# backs up, applies, and publishes with publish_snapshot.sh when anything was placed.
# After a placement it dry-runs the catchment match; a change there opens an issue (the
# match is reviewed by hand) but does not hold the pins back. A stop opens one GitHub
# issue, "Pin refresh stopped", and the next hour retries. Log and reports:
# $state/refresh_pins.log, $state/pins-*.json (kept a month); a pg_dump before each
# apply (the last 4 kept) in $backups.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
state="${PINS_STATE_DIR:-$HOME/.local/state/schooldecider}"
max_usd="${PINS_MAX_USD:-0.10}"
max_placed="${PINS_MAX_PLACED:-10}"
city="${PINS_CITY:-sofia}"
repo="${PUBLISH_REPO:-mike2906/school-comparison}"
backups="${PINS_BACKUPS:-$HOME/backups}"
container="${BACKUP_DB_CONTAINER:-sofia_schools_db}"
database="${BACKUP_DATABASE:-sofia_schools}"

mkdir -p "$state" "$backups"
exec >> "$state/refresh_pins.log" 2>&1
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
issue() {
	local title="$1" body="$2"
	if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$title\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
		gh issue create -R "$repo" --title "$title" --body "$body" > /dev/null || true
	fi
}
stop() {
	log "STOPPED: $*"
	issue "Pin refresh stopped" "backend/scripts/refresh_pins.sh on $(hostname) stopped at $(date -u +%Y-%m-%dT%H:%M:%SZ): $*

New pins are not reaching the site until this is fixed; the next hourly run tries again. Log: $state/refresh_pins.log. Close this issue once fixed."
	exit 1
}
placed() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["outcomes"].get("placed", 0))' "$1"; }

# One run at a time; a run still going when cron fires again is left to finish.
exec 8>>"$state/refresh_pins.lock"
flock -n 8 || exit 0

# "unpublished" marks a run whose pins were written but whose publish failed: publish first.
if [ ! -f "$state/pins_unpublished" ]; then
	last="$(cat "$state/pins_last_ok" 2>/dev/null || echo 0)"
	[ $(($(date -u +%s) - last)) -ge $((7 * 24 * 3600)) ] || exit 0

	stamp="$(date -u +%Y%m%dT%H%M%SZ)"
	cd "$repo_root/backend"
	cli() { uv run python -m app.scrapers.cli "$@" > /dev/null; }

	cli website-pins --city "$city" --out "$state/pins-website-dry-$stamp.json" || stop "website-pins dry run failed"
	cli pin-agent --city "$city" --max-usd "$max_usd" --out "$state/pins-agent-dry-$stamp.json" || stop "pin-agent dry run failed"
	website="$(placed "$state/pins-website-dry-$stamp.json")"
	agent="$(placed "$state/pins-agent-dry-$stamp.json")"
	log "dry run: website-pins $website, pin-agent $agent would be placed"
	[ "$website" -le "$max_placed" ] && [ "$agent" -le "$max_placed" ] ||
		stop "the dry run would place $website (website) and $agent (agent) locations, over $max_placed; check the reports $state/pins-*-dry-$stamp.json"

	if [ $((website + agent)) -gt 0 ]; then
		backup="$backups/$database-pre-pins-$stamp.dump"
		if ! docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$backup" || ! [ -s "$backup" ]; then
			rm -f "$backup"
			stop "pg_dump failed"
		fi
		ls -1t "$backups"/"$database"-pre-pins-*.dump | tail -n +5 | xargs -r rm -f

		# Each pin is written on its own, so pins written before a failure are published next hour.
		touch "$state/pins_unpublished"
		cli website-pins --city "$city" --apply --out "$state/pins-website-$stamp.json" || stop "website-pins failed"
		cli pin-agent --city "$city" --max-usd "$max_usd" --apply --out "$state/pins-agent-$stamp.json" || stop "pin-agent failed"
		website="$(placed "$state/pins-website-$stamp.json")"
		agent="$(placed "$state/pins-agent-$stamp.json")"
		log "applied: website-pins $website, pin-agent $agent placed"
		[ $((website + agent)) -gt 0 ] || rm -f "$state/pins_unpublished"

		if [ "$city" = sofia ] && [ $((website + agent)) -gt 0 ]; then
			catchments="$(uv run python scripts/match_sofia_catchments.py 2>&1)" || catchments="the dry run failed: $catchments"
			if ! grep -q "'schools_changed': 0," <<< "$catchments"; then
				log "catchment match would change: $(grep -m 1 "schools_changed" <<< "$catchments" || echo "no summary")"
				issue "Catchment match changed" "After placing pins, backend/scripts/match_sofia_catchments.py (dry run) on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ) would change:

\`\`\`
$(head -n 40 <<< "$catchments")
\`\`\`

Review it and apply with \`--apply\`, then publish. The pins themselves were published."
			fi
		fi
	fi
	find "$state" -name 'pins-*.json' -mtime +30 -delete
	date -u +%s > "$state/pins_last_ok"
fi

if [ -f "$state/pins_unpublished" ]; then
	"$repo_root/backend/scripts/publish_snapshot.sh" || stop "publish failed (pins stored, not published; the next run retries)"
	rm -f "$state/pins_unpublished"
	log "published"
fi
