# Scheduled jobs on the launch-DB machine

The launch DB lives on Mike's PC (WSL), so the jobs that write to it and publish run there from
cron. Each one runs hourly and does nothing until it is due, so a laptop that slept catches up
at the next hour. Each opens one GitHub issue when it stops.

| Script | Due | Spend cap | Publishes | Issue when it stops |
|---|---|---|---|---|
| `watch_open_days.sh` | daily (weekly in July and August) | $0.30 a run, $0.60 a day | when dates changed | Open-days check stopped |
| `publish_cutoffs.sh` | when the `cutoff-data` branch moves | none (no model) | new rounds | Automatic cut-off publish stopped |
| `refresh_fees.sh` | monthly, from the 1st | $1.50 a month | what it wrote | Scheduled fees re-read stopped |
| `refresh_facts.sh` | twice a year, from 1 Feb and 25 Aug, 120 schools a night | $1.70 a night, $5 a cycle | each night | Scheduled facts re-read stopped |
| `refresh_register.sh` | monthly, from the 5th | ~$0.30 (the pin agent's two passes) | always (via `refresh_pins.sh`) | Scheduled register refresh stopped |

`refresh_pins.sh` is not on cron by itself: Mike wants pins re-run after each register import,
not weekly, so `refresh_register.sh` runs it with `PINS_FORCE=1`.

Mike approved the schedule and the caps on 2026-10-20 (`approvals/schedule-proposal.md` in the
project files). A new job, a higher cap or a shorter interval goes back to him.

## How the three refreshes behave

They share `backend/scripts/lib/scheduled_job.sh`:

- **Merged code only.** Cron runs whatever is checked out. A job stops (with its issue) when the
  checkout has local edits to tracked files or its HEAD is not on `origin/main` as last
  fetched. Nothing pulls main by itself: a thread updates the checkout with Mike's go.
- **Installing starts nothing.** The first run records the current month or cycle as done, so
  the first scheduled run is the next one.
- **One at a time, one start a day.** One lock (`scheduled.lock`) for the three, and at most
  one start per UTC day across them. A laptop that was off for a week runs them on different
  days, and a stopped job tries again the next day, not every hour. On 1 February the fees
  usually run first and the facts start the next day.
- **No hung runs.** Each command runs under `timeout` (`SCHEDULED_RUN_TIMEOUT`, 4 hours), so a
  hung run cannot hold the lock and quietly stop every later one; it counts as a failed run.
- **Backup first.** A `pg_dump` before each run's writes (the last 3 per job kept in
  `~/backups`).
- **Spend per cycle.** `$state/<job>_spend` books each run at its whole cap before it starts and
  corrects it from the report afterwards. A run killed mid-way (a reboot) has no report, so it
  counts in full. The fees month or facts cycle ends, with an issue, when what is left is too
  little to start (`$0.40` for fees, under which the fee check's reserve alone stops a run;
  `$0.30` for facts). A thread can then finish it with one go.
- **Carry on, don't start over.** The fees and facts runs append each school's result to
  `$state/<job>-<cycle>.schools.jsonl` in id order, and the next run starts `--after-id` the last
  one. That covers a run the cap cut short, a killed run, and the facts nights. A run in
  which more than 1 school in 10 failed (a provider out of credit) has its lines removed, so
  the next day reads those schools again.
- **Publish what was written.** Schools write one at a time, so whatever a run wrote is
  published with `publish_snapshot.sh`, even when the run then stopped. A failed publish
  leaves `$state/<job>_unpublished` and is retried at the next hour.

### The publish lock and `publish_cutoffs.sh`

The fees and facts runs take an hour or more, so they write **outside** the publish lock and
take it only to publish (`publish_snapshot.sh` takes it itself). Holding it for the whole run
would block `publish_cutoffs.sh`, which waits an hour for the lock and then stops. An hourly
publish by another job (open days, cut-offs) meanwhile only ships some schools' new data
early; each school's write stands on its own, as with the open-days check.

`refresh_register.sh` also writes outside the lock: its steps each skip what is already
pinned, and `refresh_pins.sh` publishes at the end. A publish in between ships new schools
before their pins, which then show "Not on the map yet" until the chain's publish.

### The register chain

After the backup: `run --stage discover --city sofia` (the ministry register and kg.sofia.bg; a
crashed source now exits non-zero), `scripts/import_sofia_municipal_points.py --apply`,
`register-pins --apply`, `site-address --apply`, `confirm-pins --apply` (all free, hidden
locations only), then `refresh_pins.sh` with `PINS_FORCE=1`: `website-pins`, `pin-agent`
(`REGISTER_PINS_USD`, $0.15 a pass), `site-city`, `site-grades` with their own stops, the
catchment dry run (an issue "Catchment match changed" if it would change), the publish and the
OSM check (`skills/geocoding/SKILL.md`, Weekly run). Any failure stops the chain; the next day
runs it again from the start, which costs pennies.

## Setup

WSL needs cron running (systemd enabled in `/etc/wsl.conf`), the launch DB container, `gh`
signed in and the backup credentials of `backup_offsite.sh`. The crontab, with the PATH cron
lacks:

```
PATH=/home/mike2906/.local/bin:/usr/local/bin:/usr/bin:/bin
10 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/watch_open_days.sh
40 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/publish_cutoffs.sh
20 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/refresh_fees.sh
30 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/refresh_facts.sh
50 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/refresh_register.sh
```

Logs: `~/.local/state/schooldecider/{fees,facts,register}.log`. To see what is due:
`cat ~/.local/state/schooldecider/{fees,facts,register}_cycle` (the last cycle done) and
`scheduled_last_day`. To finish a stopped cycle by hand, run the job's command with
`--after-id` the last line of its lines file.
