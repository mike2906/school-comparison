# Faster site-agent runs (fees agent + school facts)

Started 2026-10-14 with Mike. Goal: a city run that finishes in about an hour instead of
an afternoon, with no change to what either agent reads, checks or stores, so the next
cities need no babysitting.

## Where the time goes today

- `school-facts --apply` on Sofia: ~1 school/min, 344 schools, ~5.5 h.
- `fees-agent`: ~35 s/school.

Both stages walk schools one at a time. Per school, the time is the crawl (up to 12
pages, each rendered by crawl4ai with a 30 s timeout and a retry, one after another) plus
the model calls (2 to 4 link picks, the reader, then the judge or a re-read).

Running schools side by side alone is not enough: `execute_billable_request` holds
`scope.dispatch_lock` from reservation to attribution, so every model call in a run is
serialised. With several schools in flight the crawls overlap but the calls queue, and
the calls are a large part of each school's time.

## Design

### 1. Schools in parallel (both stages)

- `stages/school_pool.py` runs one coroutine per school, `--workers N` at a time
  (CLI default 8; 1 keeps the old loop and the caller's session), each worker with its
  own `AsyncSession` (one session is not safe across tasks). One crawl4ai browser and one
  httpx client are shared by the workers; crawl4ai 0.9.4 gives concurrent `arun` calls
  their own pages (they share a browser context, so cookies, which do not matter across
  different school sites).
- Each school's write path is unchanged: the fees agent's `_lock_attributes` and the facts
  stage's `refresh(..., with_for_update=True)` re-read the row under a lock before writing.
  Two workers never hold the same school.
- A spend-cap stop in one worker stops the others from starting a new school. A school
  already mid-read carries on, and its next call may be refused too (a facts judge, a
  fees first read), in which case that school stores nothing, as a serial run's last
  school does today. The fees re-read still swallows a cap refusal and keeps its first
  read; the next school's first read then stops the run.
- Results are reported in school-id order. The fees agent's `--out` lines are written in
  id order too, each once every lower id is done, so `--after-id` with the last line's id
  still skips no school (a few schools read past it are read again). An error the stage
  did not catch becomes that school's `error` result instead of ending the run.
- The in-process `Spend` check counts calls in flight in every worker at their estimate
  (`Spend.hold`), and the estimates assume a full output, so with N workers the run
  still stops before its cap on our own numbers; the ledger reservation above stays the
  backstop. The DB engine allows 35 connections (5 + 30 overflow): each worker holds
  one, each call in flight takes a short one for the ledger, plus the heartbeat.
- Politeness: one school's pages are still fetched one at a time, and schools on one
  site (same host, or a domain of their own under another ending, e.g. meridian22.org and
  meridian22.bg, which the fees finder treats as one site) are handed to one worker and
  run one after another. So a site never gets two of our crawls at once within a run.
  Two different runs at once (fees and facts today) can still overlap on a site.
- Stage 6 after a fees write now re-reads the school row under a lock
  (`validate_school_data`): it wrote the whole attributes JSON back from the fees
  session's copy, so a facts write landing in between could be lost. This applies to the
  two separate runs on Mike's PC today too.

### 2. Model calls in parallel, cap still exact enough

- `provider_cost_scope(..., max_in_flight=N)`: the lock becomes a semaphore of N
  (default 1, which is today's behaviour for every other stage).
- Reservations stay one at a time (an in-process lock plus `reserve_provider_request`'s
  row lock on the pipeline run), and each cap check counts every pending or uncertain
  request at the request reserve. So the cap holds as long as no call costs more than the
  reserve. The reserve is an assumption, not a limit: today one call can overshoot by its
  excess over the reserve; with N in flight, up to N calls can (Codex's example: four
  $0.20 calls on a $0.10 reserve end $0.40 over a $0.50 cap). Site-agent calls cost about
  $0.003 to $0.006 against the $0.01 reserve the Sofia runs use, so in practice this
  stays inside the cap; a run with a reserve below its real call cost should keep
  `--workers 1`.
- A run may also stop up to (N-1) x reserve early, refused while cheaper calls are still
  pending.
- The two agents pass `max_in_flight=workers`; nothing else changes.

### 3. One crawl for both readers: tried and dropped

A `site-agents` command that ran both finders on one per-school page cache was built and
measured (below): it saved 19 of 137 page fetches (14%) on the private schools and nothing
on state schools, and no model calls. With schools in parallel that is a few seconds per
private school, not worth a third command and a cache. Removed before merge; the two
stages stay separate.

## Not in this change

- Merging the two link pickers into one call (saves ~$0.004 a school, ~$1.40 a Sofia
  run; risks the validated fee page choice).
- The daily open-days hash check (its own thread).
- Concurrent page fetches within one school site.

## Second opinion (Codex, 2026-10-14)

Codex (read-only, on Mike's PC) agreed with parallel schools and challenged the rest.
Taken: the overshoot bound above was wrong and is now stated as N x excess; the
`discover_pages_many` precedent was wrong (it opens a crawler per site); schools sharing a
site now go to one worker; the
Stage 6 lost-update race is fixed. Not taken: a
crawler per worker (four browsers on a laptop; a shared one is what crawl4ai supports).
Codex would measure separate parallel stages before keeping `site-agents`; the
measurement below agreed, and it was dropped.

## Measurement (2026-10-14, Mike's PC, copy of the launch DB, dry runs, $0.23)

12 Sofia schools (8 private or international, 4 state), 8 cores, 16 GB RAM:

| run | wall time | spend | system RAM peak (2.55 GB idle) | errors |
|---|---|---|---|---|
| school-facts, 1 worker | 12:40 | $0.076 | 3.37 GB | 0 |
| school-facts, 8 workers | 2:53 | $0.056 | 3.58 GB | 0 |
| site-agents, 8 workers ($0.14 cap) | 3:20 (9 schools) | $0.095 | 3.51 GB | 0 |

- 8 workers: 4.4x faster, same 12 pages per school with identical URLs; facts kept vary
  run to run as the model does. Memory and CPU are not the limit at 8, so 8 is the default.
- The cap stops a run early by up to N in-flight estimates: the $0.14 site-agents run
  stopped at $0.07 counted, $0.095 real, because 8 calls in flight were held at their
  full-output estimates. On a city run ($2 to $4 caps) that is ~2%; a short test run
  needs a cap a few times its expected spend.
- Shared crawl: 19 of 137 fetches saved (above).
