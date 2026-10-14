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

- A small shared helper runs one coroutine per school, `--workers N` at a time
  (default 4), each with its own `AsyncSession` (one session is not safe across tasks).
  One crawl4ai browser and one httpx client are shared, as `navigator.discover_pages_many`
  already does.
- Each school's write path is unchanged: the fees agent's `_lock_attributes` and the facts
  stage's `refresh(..., with_for_update=True)` re-read the row under a lock before writing.
  Two workers never hold the same school.
- A spend-cap stop in one worker stops the others from starting a new school; a school
  already mid-read finishes (its calls still pass the ledger cap check).
- Results are reported in school-id order; the fees agent's `--out` lines are still
  appended as each school finishes, so `--after-id` resume needs the highest id with
  every lower id done. The report says which ids finished.
- Per-site politeness is unchanged: one school's pages are still fetched one at a time,
  so a school site never sees more than one request from us at once.

### 2. Model calls in parallel, cap still exact enough

- `provider_cost_scope(..., max_in_flight=N)`: the lock becomes a semaphore of N
  (default 1, which is today's behaviour for every other stage).
- The cap stays safe: `reserve_provider_request` already locks the pipeline run row and
  counts every pending or uncertain request at the request reserve. With N in flight, the
  cap check sees N-1 pending reservations at the reserve instead of their real cost, so
  the worst overshoot is the same as today's (a single call costing more than its
  reserve), and a run may stop up to (N-1) x reserve early.
- The two agents pass `max_in_flight=workers`; nothing else changes.

### 3. One crawl for both readers (`site-agents` command)

- A per-school page cache shared by the two finders: `find_pages` gets an optional
  `cache` that memoises a page's fetch (rendered text, links, pictures) and its WordPress
  API text by `page_key`, and shares one robots.txt parser.
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

## Test plan

- Unit tests: the semaphore keeps the reservation order and cap; the worker pool stops on
  a cap; each worker uses its own session; the cache serves a second finder without a
  second fetch.
- On a copy of the launch DB on Mike's PC, after the live runs finish: a dry run of
  `site-agents --workers 4` on ~10 schools against sequential dry runs of the same
  schools, comparing pages found, facts and fees per school, and time. Spend: a few cents.
