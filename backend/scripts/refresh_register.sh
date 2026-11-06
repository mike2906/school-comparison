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
# pennies: the pin agent's two passes are capped at REGISTER_PINS_USD ($0.05) each, and each
# try is booked at both, so the month's $0.30 allows three tries. A stop opens one GitHub
# issue, "Scheduled register refresh stopped", and the whole chain runs again the next day. Log: $state/register.log (and refresh_pins.log); reports $state/register-*.json.
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
update_checkout
pass_usd="${REGISTER_PINS_USD:-0.05}"
try_usd="$(python3 -c 'import sys; print(2 * float(sys.argv[1]))' "$pass_usd")"
if lt "$(python3 -c 'import sys; print(float(sys.argv[1]) - float(sys.argv[2]))' "${REGISTER_MONTH_USD:-0.30}" "$(spent)")" "$try_usd"; then
	cycle_done
	stop "this month's tries used its \$${REGISTER_MONTH_USD:-0.30}; the import and pins wait for next month"
fi
book "$try_usd"  # what the pin agent spends is not reported back: each try counts in full
backup

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cd "$repo_root/backend"
cli() { timeout -k 5m "$run_timeout" uv run python -m app.scrapers.cli "$@" > /dev/null; }
# The ministry register is behind a Cloudflare check that only a browser passes; the import
# falls back to headless Chromium (registry_browser.py). Installing it again is a no-op, and a
# failed install only matters if the register is challenged: then the import's error says so.
browser_log="$state/register-browser-$stamp.log"
timeout -k 5m 15m uv run playwright install chromium > "$browser_log" 2>&1 ||
	log "installing the register's headless browser failed (output: $browser_log)"
# Keep the import's output: its adapter errors (a blocked ministry API) go into the issue.
discover_log="$state/register-discover-$stamp.log"
discover_errors() { grep -o 'Error: .*' "$discover_log" | head -n 3 | paste -sd ';' -; }
if ! COLUMNS=400 timeout -k 5m "$run_timeout" uv run python -m app.scrapers.cli run --stage discover --city sofia > "$discover_log" 2>&1; then
	# Only the ministry adapter failed, on Cloudflare's check (every try since November 2026):
	# the other sources were imported, so the chain goes on, with one standing issue.
	if grep -q 'Cloudflare challenge' "$discover_log" && grep -q '1 discovery adapter(s) failed' "$discover_log"; then
		log "MINISTRY REGISTER BLOCKED: $(discover_errors)"
		name="$title: ministry register blocked"
		if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$name\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
			gh issue create -R "$repo" --title "$name" --body "backend/scripts/refresh_register.sh on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ): the ministry register (ri-api.mon.bg) answered with a Cloudflare challenge, even to a plain headless browser: $(discover_errors). The other sources and the pin steps still ran. New and closed schools from the ministry are not imported until access is back; we do not try to get round the check (backend/docs/MOE_API_DOCUMENTATION.md). Close this issue when the import works again." > /dev/null || true
		fi
	else
		stop "the register import (discover) failed: $(discover_errors) (output: $discover_log)"
	fi
fi
timeout -k 5m "$run_timeout" uv run python scripts/import_sofia_municipal_points.py --apply > /dev/null || stop "import_sofia_municipal_points.py failed"
cli register-pins --apply --out "$state/register-pins-$stamp.json" || stop "register-pins failed"
cli site-address --apply --out "$state/register-site-address-$stamp.json" || stop "site-address failed"
cli confirm-pins --city sofia --apply --out "$state/register-confirm-pins-$stamp.json" || stop "confirm-pins failed"
for report in "$state"/register-*-"$stamp".json; do
	[ -f "$report" ] || continue
	log "$(basename "$report"): $(field "$report" 'r["outcomes"]')"
done
find "$state" \( -name 'register-*.json' -o -name 'register-discover-*.log' -o -name 'register-browser-*.log' \) -mtime +90 -delete

started="$(date -u +%s)"
# A small reserve per call, as in watch_open_days.sh: a pin-agent call costs well under a cent.
PINS_FORCE=1 PINS_MAX_USD="$pass_usd" PROVIDER_REQUEST_RESERVE_USD="${PROVIDER_REQUEST_RESERVE_USD:-0.01}" \
	timeout -k 5m "$run_timeout" "$repo_root/backend/scripts/refresh_pins.sh" ||
	stop "refresh_pins.sh stopped; its log ($state/refresh_pins.log) and issue say why"
# It exits quietly when it cannot take its own lock, so check it really ran and published.
[ "$(cat "$state/pins_last_ok" 2> /dev/null || echo 0)" -ge "$started" ] && [ ! -f "$state/pins_unpublished" ] ||
	stop "refresh_pins.sh did not finish a run and publish"
cycle_done
log "done"
