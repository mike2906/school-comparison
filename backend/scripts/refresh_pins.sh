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
# backs up and applies to the schools the dry run placed only (the model's picks can
# differ between runs), then publishes with publish_snapshot.sh. Before that publish it
# dry-runs the catchment match; a change there opens an issue (the match is reviewed by
# hand) but does not hold the pins back. A stop opens one GitHub issue, "Pin refresh
# stopped"; a good weekly run also lists schools that disagree with OpenStreetMap in one rolling
# issue (osm_check below). A stopped run is retried a day later, a failed publish the next hour. Log and reports:
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

New pins are not reaching the site until this is fixed; the script tries again a day later (a failed publish, next hour). Log: $state/refresh_pins.log. Close this issue once fixed."
	exit 1
}
report() { python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {"r": r}))' "$1" "$2"; }
placed() { report "$1" 'r["outcomes"].get("placed", 0)'; }
# --school-id flags for the schools a dry run placed: the apply writes no others.
placed_ids() { report "$1" '" ".join(f"--school-id {i}" for i in sorted({x["school_id"] for x in r["results"] if x["outcome"] == "placed"}))'; }

# Published pins, numbers and websites that disagree with OSM go to one rolling issue, updated
# only when its text changes. Free (one Overpass query); a failure is logged only. Runs once
# the week's pins are published, so it compares what the site shows.
osm_check() {
	local out="$state/pins-osm-$(date -u +%Y%m%dT%H%M%SZ).json" body="$state/osm-issue.md" title="Schools that disagree with OpenStreetMap"
	local number digest
	cli osm-check --city "$city" --out "$out" --issue-body "$body" || { log "osm-check failed"; return 1; }
	# The school lines only: a change in how many schools OSM tags is not news.
	digest="$(grep '^- ' "$body" | sha256sum)" || return 1
	[ "$digest" != "$(cat "$state/osm_issue_digest" 2>/dev/null)" ] || return 0
	# A failed lookup must not open a second issue: try again next hour.
	number="$(gh issue list -R "$repo" --state open --search "in:title \"$title\"" --json number -q '.[0].number')" ||
		{ log "osm-check: gh issue list failed"; return 1; }
	# The issue's text is replaced, not commented: it always shows the current list.
	if [ -n "$number" ]; then
		gh issue edit "$number" -R "$repo" --body-file "$body" > /dev/null || { log "osm-check: gh issue edit failed"; return 1; }
	elif grep -q '^- ' "$body"; then
		gh issue create -R "$repo" --title "$title" --body-file "$body" > /dev/null || { log "osm-check: gh issue create failed"; return 1; }
	fi
	echo "$digest" > "$state/osm_issue_digest"
	log "osm-check: $(head -n 1 "$body")"
}

# One run at a time; a run still going when cron fires again is left to finish.
exec 8>>"$state/refresh_pins.lock"
flock -n 8 || exit 0
cd "$repo_root/backend"
cli() { uv run python -m app.scrapers.cli "$@" > /dev/null; }

# A check due from a good weekly run whose pins are published; a failed one is retried hourly.
if [ -f "$state/osm_due" ] && [ ! -f "$state/pins_unpublished" ]; then
	osm_check && rm -f "$state/osm_due"
fi

# "unpublished" marks a run whose pins were written but not yet published: publish first.
if [ ! -f "$state/pins_unpublished" ]; then
	now="$(date -u +%s)"
	last="$(cat "$state/pins_last_ok" 2>/dev/null || echo 0)"
	[ $((now - last)) -ge $((7 * 24 * 3600)) ] || exit 0
	# A stopped run is retried a day later, not every hour: each try pays for a dry run.
	tried="$(cat "$state/pins_last_try" 2>/dev/null || echo 0)"
	[ $((now - tried)) -ge $((20 * 3600)) ] || exit 0
	echo "$now" > "$state/pins_last_try"

	stamp="$(date -u +%Y%m%dT%H%M%SZ)"
	dry_web="$state/pins-website-dry-$stamp.json"
	dry_agent="$state/pins-agent-dry-$stamp.json"
	cli website-pins --city "$city" --out "$dry_web" || stop "website-pins dry run failed"
	cli pin-agent --city "$city" --max-usd "$max_usd" --out "$dry_agent" || stop "pin-agent dry run failed"
	# A run cut short, without OSM, or with many errors is not a good run: retry tomorrow.
	healthy() { report "$1" '(r.get("stopped") or r.get("osm_unavailable") or "") or (r["outcomes"].get("error", 0) * 10 > max(r["locations"], 1) and "too many errors") or ""'; }
	problem="$(healthy "$dry_web")$(healthy "$dry_agent")"
	[ -z "$problem" ] || stop "a dry run was not complete: $problem"
	website="$(placed "$dry_web")"
	agent="$(placed "$dry_agent")"
	log "dry run: website-pins $website, pin-agent $agent would be placed"
	[ "$website" -le "$max_placed" ] && [ "$agent" -le "$max_placed" ] ||
		stop "the dry run would place $website (website) and $agent (agent) locations, over $max_placed; check $dry_web and $dry_agent"

	if [ $((website + agent)) -gt 0 ]; then
		backup="$backups/$database-pre-pins-$stamp.dump"
		if ! docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$backup" || ! [ -s "$backup" ]; then
			rm -f "$backup"
			stop "pg_dump failed"
		fi
		ls -1t "$backups"/"$database"-pre-pins-*.dump | tail -n +5 | xargs -r rm -f

		# Each pin is written on its own, so pins written before a failure are published next hour.
		touch "$state/pins_unpublished"
		# shellcheck disable=SC2046  # one word per flag and id
		[ "$website" = 0 ] || cli website-pins --city "$city" --apply $(placed_ids "$dry_web") --out "$state/pins-website-$stamp.json" ||
			stop "website-pins failed"
		# shellcheck disable=SC2046
		[ "$agent" = 0 ] || cli pin-agent --city "$city" --max-usd "$max_usd" --apply $(placed_ids "$dry_agent") --out "$state/pins-agent-$stamp.json" ||
			stop "pin-agent failed"
		# The apply re-runs the model on the same schools; it must not place more than the cap.
		website="$( [ "$website" = 0 ] && echo 0 || placed "$state/pins-website-$stamp.json")"
		agent="$( [ "$agent" = 0 ] && echo 0 || placed "$state/pins-agent-$stamp.json")"
		log "applied: website-pins $website, pin-agent $agent placed"
		if [ "$website" -gt "$max_placed" ] || [ "$agent" -gt "$max_placed" ]; then
			rm -f "$state/pins_unpublished"
			stop "the apply placed $website (website) and $agent (agent), over $max_placed: NOT published by this script, but the next publish by any script ships them. Check $state/pins-*-$stamp.json; restore $backup if wrong"
		fi
	fi
	touch "$state/osm_due"
	find "$state" -name 'pins-*.json' -mtime +30 -delete
	date -u +%s > "$state/pins_last_ok"
fi

if [ -f "$state/pins_unpublished" ]; then
	if [ "$city" = sofia ]; then
		# Pins can move a school into or out of a catchment: the match is reviewed by hand.
		catchments="$(uv run python scripts/match_sofia_catchments.py 2>&1)" || catchments="the dry run failed: $catchments"
		if ! grep -Eq "'schools_changed': 0[,}]" <<< "$catchments"; then
			log "catchment match would change: $(grep -m 1 "schools_changed" <<< "$catchments" || echo "no summary")"
			issue "Catchment match changed" "After placing pins, backend/scripts/match_sofia_catchments.py (dry run) on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ) would change:

\`\`\`
$(head -n 40 <<< "$catchments")
\`\`\`

Review it, apply with \`--apply\` and publish. The pins themselves are published."
		fi
	fi
	"$repo_root/backend/scripts/publish_snapshot.sh" || stop "publish failed (pins stored, not published; the next run retries)"
	rm -f "$state/pins_unpublished"
	log "published"
fi

if [ -f "$state/osm_due" ] && [ ! -f "$state/pins_unpublished" ]; then
	osm_check && rm -f "$state/osm_due"
fi
