#!/usr/bin/env bash
# Re-read every Sofia school's facts twice a year, over three nights, and publish them.
#
# Run hourly by cron on the machine with the launch DB (docs/SCHEDULED_JOBS.md). Cycles start
# on 1 February and 25 August (the first after install is skipped). Each night (one run a
# day) backs up, reads the next FACTS_PER_NIGHT (120) schools with `school-facts --apply`,
# at most FACTS_NIGHT_USD ($1.70), and publishes; the cycle ends once the last school is read,
# within FACTS_CYCLE_USD ($5) in all. A night the cap cut short, or one that failed, carries
# on after its last school the next day. A stop opens one GitHub issue, "Scheduled facts
# re-read stopped". Log, report and lines: $state/facts.log, facts-<cycle>.json(.schools.jsonl).
set -euo pipefail

job=facts
title="Scheduled facts re-read"
# shellcheck source=lib/scheduled_job.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/scheduled_job.sh"
cycle_usd="${FACTS_CYCLE_USD:-5.00}"
night_usd="${FACTS_NIGHT_USD:-1.70}"
limit="${FACTS_PER_NIGHT:-120}"
city="${FACTS_CITY:-sofia}"

take_lock
check_checkout
publish_pending
year="$(date -u -d "@$now" +%Y)"
day="$(date -u -d "@$now" +%m%d)"
if [ "$day" -ge 0825 ]; then
	cycle="$year-08"
elif [ "$day" -ge 0201 ]; then
	cycle="$year-02"
else
	cycle="$((year - 1))-08"
fi
cycle_due
claim_day
agent_run "$cycle_usd" "$night_usd" 0.30 school-facts --city "$city" --limit "$limit"
