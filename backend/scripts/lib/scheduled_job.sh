# Shared by the scheduled refreshes run from cron on the machine with the launch DB:
# refresh_fees.sh, refresh_facts.sh and refresh_register.sh (docs/SCHEDULED_JOBS.md).
#
# The sourcing script sets `job` (a short name for its state files) and `title` (the name in
# its GitHub issue) first. Then: one refresh at a time and one start per UTC day across all
# of them, merged code only, a pg_dump before writing, a spend ledger per cycle that counts
# a killed run at its whole cap, and one issue when a job stops.
# shellcheck shell=bash disable=SC2154  # job, title and cycle come from the sourcing script

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
state="${SCHEDULED_STATE_DIR:-$HOME/.local/state/schooldecider}"
backups="${SCHEDULED_BACKUPS:-$HOME/backups}"
repo="${PUBLISH_REPO:-mike2906/school-comparison}"
container="${BACKUP_DB_CONTAINER:-sofia_schools_db}"
database="${BACKUP_DATABASE:-sofia_schools}"
now="${SCHEDULED_NOW:-$(date -u +%s)}"  # set by the tests only
# A hung run would hold the lock and quietly stop every later one: end it, as a failed run.
# `timeout` signals its whole process group, so the run's children end with it.
run_timeout="${SCHEDULED_RUN_TIMEOUT:-4h}"
today="$(date -u -d "@$now" +%F)"

mkdir -p "$state" "$backups"
exec >> "$state/$job.log" 2>&1
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
stop() {
	log "STOPPED: $*"
	local name="$title stopped"
	if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$name\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
		gh issue create -R "$repo" --title "$name" --body "backend/scripts/refresh_$job.sh on $(hostname) stopped at $(date -u +%Y-%m-%dT%H:%M:%SZ): $*

Log: $state/$job.log. The job tries again the next day (a failed publish, the next hour). Close this issue once fixed." > /dev/null || true
	fi
	exit 1
}

# One refresh at a time: a job that finds another running tries again at the next hour.
take_lock() {
	exec 8>>"$state/scheduled.lock"
	flock -n 8 || exit 0
}

# Cron runs whatever is checked out, so only merged code may run: no local edits to tracked
# files, no untracked file under backend/ that git does not ignore (a sitecustomize.py would
# run with every command), and HEAD on main as last fetched. update_checkout then brings it up
# to main before a run.
check_checkout() {
	git -C "$repo_root" diff --quiet HEAD -- ||
		stop "the checkout at $repo_root has local changes; scheduled jobs run merged code only"
	[ -z "$(git -C "$repo_root" ls-files --others --exclude-standard -- backend)" ] ||
		stop "the checkout at $repo_root has untracked files under backend/; scheduled jobs run merged code only"
	git -C "$repo_root" merge-base --is-ancestor HEAD origin/main 2> /dev/null ||
		stop "the checkout at $repo_root ($(git -C "$repo_root" rev-parse --short HEAD)) is not on main"
}

# What a pull may not bring in unattended: the scheduled jobs with their caps, the spend guard
# that enforces the caps, and DB migrations (a pull does not apply them, so the new code would
# fail on the old schema).
job_files=(
	backend/alembic
	backend/alembic.ini
	backend/app/services/provider_costs.py
	backend/scripts/lib/scheduled_job.sh
	backend/scripts/refresh_fees.sh
	backend/scripts/refresh_facts.sh
	backend/scripts/refresh_register.sh
	backend/scripts/refresh_pins.sh
	backend/scripts/publish_snapshot.sh
	backend/scripts/watch_open_days.sh
	backend/scripts/publish_cutoffs.sh
)

# One issue (once) that a job ran without updating; the run goes on.
warn() {
	log "NOT UPDATED: $*"
	local name="$title: checkout not updated"
	if [ -z "$(gh issue list -R "$repo" --state open --search "in:title \"$name\"" --json number -q '.[0].number' 2>/dev/null)" ]; then
		gh issue create -R "$repo" --title "$name" --body "backend/scripts/refresh_$job.sh on $(hostname) ran at $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(git -C "$repo_root" rev-parse --short HEAD) without updating to origin/main: $*

Log: $state/$job.log. Close this issue once the checkout is updated." > /dev/null || true
	fi
}

# Before a run, fast-forward the checkout to origin/main, so merged fixes reach the schedule
# without a thread's go (Mike, 2026-10-21). check_checkout has made sure HEAD is merged code,
# so whatever stops the update (main changes job_files, the fetch fails, the checkout is on
# another branch) only means this run uses the code it has, with an issue saying why; a new
# job, cap or migration still reaches the PC through a thread's pull with Mike's go. No update
# while the open-days check or a publish runs from this checkout: files would change under it.
update_checkout() {
	local branch changed
	branch="$(git -C "$repo_root" symbolic-ref -q --short HEAD || true)"
	if [ -n "$branch" ] && [ "$branch" != main ]; then
		warn "the checkout is on branch $branch"
		return 0
	fi
	exec 6>>"${OPEN_DAYS_STATE_DIR:-$HOME/.local/state/schooldecider}/open_days.lock" 7>>"$("$repo_root/backend/scripts/publish_snapshot.sh" --lock-path)"
	if ! flock -n 6 || ! flock -n 7; then
		exec 6>&- 7>&-
		log "not updated: the open-days check or a publish is running"
		return 0
	fi
	if ! GIT_TERMINAL_PROMPT=0 timeout 5m git -C "$repo_root" fetch -q origin +refs/heads/main:refs/remotes/origin/main; then
		warn "git fetch origin main failed"
	else
		changed="$(git -C "$repo_root" diff --name-only HEAD origin/main -- "${job_files[@]}" | tr '\n' ' ')"
		if [ -n "$changed" ]; then
			warn "main changes the scheduled jobs, their spend guard or migrations (${changed% }); update the checkout with Mike's go"
		elif [ -n "$branch" ]; then
			git -C "$repo_root" merge -q --ff-only origin/main || warn "the checkout cannot fast-forward to origin/main"
		else
			git -C "$repo_root" checkout -q --detach origin/main || warn "the checkout cannot move to origin/main"
		fi
	fi
	exec 6>&- 7>&-
	log "checkout at $(git -C "$repo_root" rev-parse --short HEAD)"
}

# The first run after install only records the current cycle, so installing starts nothing:
# the next cycle is the first one run. Exits when this cycle is done.
cycle_due() {
	if [ ! -f "$state/${job}_cycle" ]; then
		echo "$cycle" > "$state/${job}_cycle"
		log "installed: the first scheduled run is the cycle after $cycle"
		exit 0
	fi
	[ "$(cat "$state/${job}_cycle")" != "$cycle" ] || exit 0
}
cycle_done() { echo "$cycle" > "$state/${job}_cycle"; }

# At most one start a day (UTC) across the jobs: a laptop off for a week runs them on
# different days, and a stopped job retries tomorrow, not every hour.
claim_day() {
	[ "$(cat "$state/scheduled_last_day" 2> /dev/null)" != "$today" ] || exit 0
	echo "$today" > "$state/scheduled_last_day"
}

backup() {
	local dump="$backups/$database-pre-$job-$(date -u +%Y%m%dT%H%M%SZ).dump"
	if ! docker exec "$container" pg_dump -U postgres -d "$database" --format=custom > "$dump" || ! [ -s "$dump" ]; then
		rm -f "$dump"
		stop "pg_dump failed"
	fi
	ls -1t "$backups"/"$database"-pre-"$job"-*.dump | tail -n +4 | xargs -r rm -f
	log "backup $dump"
}

# Spend per cycle, one "cycle usd" line per run. A run is booked at its whole cap before it
# starts and corrected once its report says what it spent, so a killed run counts in full.
spent() { awk -v c="$cycle" '$1 == c { s += $2 } END { printf "%.4f", s }' "$state/${job}_spend" 2> /dev/null || echo 0; }
book() { echo "$cycle $1" >> "$state/${job}_spend"; }
rebook() {
	sed -i '$d' "$state/${job}_spend"
	book "$1"
}
lt() { python3 -c 'import sys; sys.exit(not round(float(sys.argv[1]), 4) < round(float(sys.argv[2]), 4))' "$1" "$2"; }
min() { python3 -c 'import sys; print(f"{min(map(float, sys.argv[1:])):.2f}")' "$@"; }
field() { python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); print(eval(sys.argv[2], {"r": r}))' "$1" "$2"; }

# The school id on a lines file's last line; nothing when there is none.
last_id() {
	[ -s "$1" ] || return 0
	tail -n 1 "$1" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("school_id", ""))'
}

publish_pending() {
	[ -f "$state/${job}_unpublished" ] || return 0
	"$repo_root/backend/scripts/publish_snapshot.sh" || stop "publish failed (written, not published; the next hour retries)"
	rm -f "$state/${job}_unpublished"
	log "published"
}

# One run of a site agent (fees-agent or school-facts) for this cycle, carrying on after the
# last school the cycle's lines file holds. Arguments: the cycle's budget, the most one run may
# spend, the least worth starting with, then the CLI command and its own options.
agent_run() {
	local cap="$1" run_cap="$2" least="$3"
	shift 3
	local lines="$state/$job-$cycle.schools.jsonl" report="$state/$job-$cycle.json"
	local left budget after before rc schools errors stopped
	left="$(python3 -c 'import sys; print(float(sys.argv[1]) - float(sys.argv[2]))' "$cap" "$(spent)")"
	if lt "$left" "$least"; then
		cycle_done
		stop "the cycle's \$$cap is spent (\$$(spent)) before every school was read; the rest waits for the next cycle"
	fi
	budget="$(min "$run_cap" "$left")"
	after="$(last_id "$lines")"
	before="$(wc -l < "$lines" 2> /dev/null || echo 0)"
	backup
	book "$budget"
	rm -f "$report"
	log "run: \$$budget${after:+, after school $after}"
	rc=0
	(cd "$repo_root/backend" && timeout -k 5m "$run_timeout" uv run python -m app.scrapers.cli "$@" --apply --max-usd "$budget" --out "$report" ${after:+--after-id "$after"} > /dev/null) || rc=$?
	# Schools write one at a time: whatever a run wrote is published, even when it failed.
	[ "$(wc -l < "$lines" 2> /dev/null || echo 0)" = "$before" ] || touch "$state/${job}_unpublished"
	[ -f "$report" ] || {
		publish_pending
		after="$(last_id "$lines")"
		stop "the run failed (exit $rc); tomorrow's run carries on${after:+ after school $after}"
	}
	rebook "$(field "$report" 'r["spent_usd"]')"
	schools="$(field "$report" 'r["schools"]')"
	errors="$(field "$report" 'sum(x["outcome"] == "error" for x in r["results"])')"
	stopped="$(field "$report" 'r["stopped"] or ""')"
	log "done: $schools schools, $errors errors, \$$(field "$report" 'r["spent_usd"]') spent${stopped:+, $stopped}"
	find "$state" -name "$job-*.json*" -mtime +60 -delete
	# Most schools failing is the run failing (a provider out of credit): read them again tomorrow.
	# Not for a handful: one broken site at the end of the list would be retried every day.
	if [ "$schools" -ge 10 ] && [ $((errors * 10)) -gt "$schools" ]; then
		head -n "$before" "$lines" > "$lines.tmp" && mv "$lines.tmp" "$lines"
		publish_pending
		stop "$errors of $schools schools failed; tomorrow's run reads them again"
	fi
	case "$stopped" in
	"") [ -n "${limit:-}" ] && [ "$schools" -ge "$limit" ] || cycle_done ;;
	"run stopped"*) publish_pending; stop "$stopped" ;;
	*) ;; # the spend cap: tomorrow carries on with what is left of the cycle's budget
	esac
	publish_pending
}
