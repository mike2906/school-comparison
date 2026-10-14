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
  (CLI default 4; 1 keeps the old loop and the caller's session), each worker with its
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

### 3. One crawl for both readers (`site-agents` command)

- A per-school page cache shared by the two finders: `find_pages` gets an optional
  `cache` (`SiteCache`) that keeps a page's fetch (final URL, text, links, pictures) and
  its WordPress API text by exact URL (`page_key` folds case and `www`, which can be
  different pages), and shares one robots.txt parser. A failed or empty fetch is not
  kept, so the second finder tries again. Each finder keeps its own queue, page limit,
  pictures and WordPress state, so it reads what it would on its own, give or take the
  site changing between the two reads of a separate run. Each school's report gives
  `fetches` and `fetches_saved` (pages served from the cache), the number that decides
  whether `site-agents` stays.
- Each agent stops the run as it does on its own: facts on any provider-cost error, fees
  only on a cap (an unattributed fee read is that school's error).
- School selection is the union of the two stages': facts for a website URL validation
  accepted, fees for any private or international school with a website.
- `site-agents` runs, per school, the facts finder and reader, then (private and
  international schools only) the fees finder and reader on the same cache, each with its
  own link prompt, its own reader and its own write path. Their prompts, checks and
  outputs are untouched.
- What it saves: the home page and any page both pickers choose (often admissions) are
  fetched once. It does not save model calls; the link picks stay separate because the
  two readers need different pages and the fee pages passed validation with their own
  picker. Crawling is free, so the gain is time and fewer hits on school sites, not money.
- `fees-agent` and `school-facts` stay as they are for single-agent runs.

## Not in this change

- Merging the two link pickers into one call (saves ~$0.004 a school, ~$1.40 a Sofia
  run; risks the validated fee page choice).
- The daily open-days hash check (its own thread).
- Concurrent page fetches within one school site.

## Second opinion (Codex, 2026-10-14)

Codex (read-only, on Mike's PC) agreed with parallel schools and challenged the rest.
Taken: the overshoot bound above was wrong and is now stated as N x excess; the
`discover_pages_many` precedent was wrong (it opens a crawler per site); schools sharing a
site now go to one worker; the cache uses exact URLs and does not keep failures; the
Stage 6 lost-update race is fixed; the combined selection is the union. Not taken: a
crawler per worker (four browsers on a laptop; a shared one is what crawl4ai supports).
Open: Codex would measure separate parallel stages before keeping `site-agents`. The
copy-DB dry run below measures that, and `site-agents` is dropped if the shared pages
are few.

## Test plan

- Unit tests: the semaphore keeps the reservation order and cap; the worker pool stops on
  a cap; each worker uses its own session; the cache serves a second finder without a
  second fetch.
- On a copy of the launch DB on Mike's PC, after the live runs finish: dry runs on ~10
  schools (mostly private): `school-facts` and `fees-agent` with `--workers 1` and 4, and
  `site-agents --workers 4`, comparing time, pages fetched (the overlap), and pages,
  facts and fees per school. Spend: a few cents.
