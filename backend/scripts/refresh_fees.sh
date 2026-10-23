#!/usr/bin/env bash
# Re-read Sofia's private-school fees once a month, with the fee check, and publish them.
#
# Run hourly by cron on the machine with the launch DB (docs/SCHEDULED_JOBS.md). Does nothing
# until a month's run is due (from the 1st; the first month after install is skipped), then
# backs up and runs `fees-agent --apply` with the fee check, at most FEES_MONTH_USD ($1.50)
# a month, and publishes what it wrote with publish_snapshot.sh. A run the spend cap cut
# short, or one that failed, carries on after its last school the next day, from what is
# left of the month's budget. A stop opens one GitHub issue, "Scheduled fees re-read stopped".
# Its first run of a month first searches for the websites of the private schools with none
# (`find-websites --apply`, free), so a site found has its fees read in the same run.
# Log, report and per-school lines: $state/fees.log, fees-<month>.json(.schools.jsonl).
set -euo pipefail

job=fees
title="Scheduled fees re-read"
# shellcheck source=lib/scheduled_job.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/scheduled_job.sh"
month_usd="${FEES_MONTH_USD:-1.50}"
city="${FEES_CITY:-sofia}"
# Fewer schools at a time than by hand: each fee check in flight books $0.36 against the cap
# (docs/SITE_AGENT_RUNS.md), so eight at once could stop the run well short of its real spend.
workers="${FEES_WORKERS:-4}"

take_lock
check_checkout
publish_pending
cycle="$(date -u -d "@$now" +%Y-%m)"
cycle_due
claim_day
update_checkout
# The month's first run only (a carried-on run starts after its last school). A failed search
# does not hold up the fee read; a later run searches again.
find_report="$state/$job-find-websites-$cycle.json"
if [ ! -s "$state/$job-$cycle.schools.jsonl" ] && [ ! -f "$find_report" ]; then
	backup
	if (cd "$repo_root/backend" && timeout -k 5m "$run_timeout" uv run python -m app.scrapers.cli find-websites --city "$city" --apply --out "$find_report" > /dev/null) && [ -f "$find_report" ]; then
		found="$(field "$find_report" 'r["found"]')"
		log "find-websites: $found of $(field "$find_report" 'r["searched"]') schools without a site found one"
		[ "$found" = 0 ] || touch "$state/${job}_unpublished"
	else
		log "find-websites failed; the fee read goes on without it"
	fi
fi
# $0.40: below that the fee check's reserve alone would stop the run.
agent_run "$month_usd" "$month_usd" 0.40 fees-agent --city "$city" --workers "$workers"
