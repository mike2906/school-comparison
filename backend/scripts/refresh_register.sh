#!/usr/bin/env bash
# Import Sofia's school register once a month, place the new and still-hidden pins, publish.
#
# Run hourly by cron on the machine with the launch DB (docs/SCHEDULED_JOBS.md). Does nothing
# until a month's run is due (from the 5th; the first month after install is skipped). Then,
# after a pg_dump: the official import (`run --stage discover`: the ministry register and
# kg.sofia.bg), Sofia Municipality's school points, then `register-pins`, `site-address` and
# `confirm-pins` for locations still hidden (all free), and refresh_pins.sh, which runs the
# pin agent and the site checks with their own stops, dry-runs the catchment match and
# publishes. Each step skips what is already pinned, so a month with nothing new costs
# pennies; the pin agent's cap is REGISTER_PINS_USD ($0.15 a pass, two passes). A stop opens
# one GitHub issue, "Scheduled register refresh stopped", and the whole chain runs again the
# next day. Log: $state/register.log (and refresh_pins.log); reports $state/register-*.json.
set -euo pipefail

job=register
title="Scheduled register refresh"
# shellcheck source=lib/scheduled_job.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/scheduled_job.sh"

take_lock
check_checkout
[ "$(date -u -d "@$now" +%d)" -ge 5 ] || exit 0
cycle="$(date -u -d "@$now" +%Y-%m)"
cycle_due
claim_day
backup

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cd "$repo_root/backend"
cli() { timeout "$run_timeout" uv run python -m app.scrapers.cli "$@" > /dev/null; }
cli run --stage discover --city sofia || stop "the register import (discover) failed"
timeout "$run_timeout" uv run python scripts/import_sofia_municipal_points.py --apply > /dev/null || stop "import_sofia_municipal_points.py failed"
cli register-pins --apply --out "$state/register-pins-$stamp.json" || stop "register-pins failed"
cli site-address --apply --out "$state/register-site-address-$stamp.json" || stop "site-address failed"
cli confirm-pins --city sofia --apply --out "$state/register-confirm-pins-$stamp.json" || stop "confirm-pins failed"
for report in "$state"/register-*-"$stamp".json; do
	[ -f "$report" ] || continue
	log "$(basename "$report"): $(field "$report" 'r["outcomes"]')"
done
find "$state" -name 'register-*.json' -mtime +90 -delete

started="$(date -u +%s)"
PINS_FORCE=1 PINS_MAX_USD="${REGISTER_PINS_USD:-0.15}" "$repo_root/backend/scripts/refresh_pins.sh" ||
	stop "refresh_pins.sh stopped; its log ($state/refresh_pins.log) and issue say why"
# It exits quietly when it cannot take its own lock, so check it really ran and published.
[ "$(cat "$state/pins_last_ok" 2> /dev/null || echo 0)" -ge "$started" ] && [ ! -f "$state/pins_unpublished" ] ||
	stop "refresh_pins.sh did not finish a run and publish"
cycle_done
log "done"
