# R6 step 3, second run: re-validation after the fixes (2026-10-13)

The same go/no-go check as `step3_check.md`, run again after #286 (an old row is kept per fee
instead of holding the whole school, BGN rows compared in euro, one row per fee, more period
words) and #287 (the finder follows sister domains, retries an empty render, reads thin
WordPress pages through the API, and falls back to fee links when the pick call fails). The bar
is unchanged: after every gate, wrong no more often than today's rows, and at least 55 % of the
fees the sites state found.

**Result: go.** After every gate the output is wrong on 12 % of rows against 18 % today, and it
finds 68 % of site fees against 40 % today. None of the post-gate wrong rows comes from the
agent's own reading (see below).

## Sample, key and run

- `step4/sample.json`: the 24 schools left of the 44 untouched ones once step 3 took its 20
  (same pool and rules as `step3/sample.json`).
- `step4/key/`: fees read blind from each site by four Claude agents with WebFetch, no access to
  the repository, DB or API, under the frozen `fresh_rescore/rules.md`. 11 sites state fees (61
  in the key), 11 state none, 2 could not be read (170's subpages failed to fetch; 516 blocks
  robots).
- `step4/run/`: `fees-agent --apply` at 22f2a85 (#286 and #287) on a copy of the launch DB on
  Mike's PC, one school at a time, `--max-usd 0.05` each, `PROVIDER_REQUEST_RESERVE_USD=0.01`.
  Total $0.026. The launch DB was not written to (526 price rows before and after); the copy was
  dropped.
- `step4/packets/`, `step4/adj/`, `step4/hidden_map.json`: three blind judges re-read the pages
  and gave every row one verdict, without knowing which extractor it came from
  (`build_packets.py` builds the packets). They rebuilt 170's fees from the site (14), and added
  a 60 EUR exam fee at 568 and a 90 EUR application fee at 520, so the key ends at 77 fees.
- `python3 docs/accuracy/step4/score.py` reproduces the numbers (`score.out`). Pass a school id
  to leave it out: `score_without_526.out` (526 is meridian22.bg, a sister of a step 3 site the
  finder fix was designed around) and `score_without_158.out` (158's site lists a 2017-2018 BGN
  price list that the frozen rules still count, and the school is withheld for a shared site).

## Results

| | Today (published) | Agent's rows | After every gate |
| --- | --- | --- | --- |
| Rows | 39 | 53 | 63 |
| Fully right | 21 (55 %) | 41 (77 %) | 43 (73 %) |
| Blank period | 10 | 8 | 9 |
| **Wrong** | **7 (18 %)** | **4 (8 %)** | **7 (12 %)** |
| Duplicates | 0 | 0 | 3 |
| **Site fees found (of 77)** | **31 (40 %)** | **49 (64 %)** | **52 (68 %)** |
| Found, without 526 (of 76) | 31 (41 %) | 48 (63 %) | 51 (67 %) |
| Found, without 158 (of 65) | 31 (48 %) | 45 (69 %) | 52 (80 %) |

"After every gate" is what the API would publish: the agent's new rows plus the old rows it
kept. The bar passes in every view.

## What is still wrong

- **Last year's fees (520, 3 rows).** The 2025-2026 rows are not replaced by a 2026-2027 read:
  older years stay as fee history, and the API publishes them marked as a previous year. The
  frozen rules count them stale. A fourth old row, 70 EUR, was kept by rule 6 because the number
  is still on the page, though no current fee is 70 EUR.
- **Old rows left beside an undated read (170, 2 rows).** Rows were replaced by year, so the
  agent's undated read left the 4 old 2026-2027 rows in place, one of them a one_time tuition
  row. Stage 6 also filled one_time into the new 2000 EUR transport row ("Еднократно плащане",
  a year of transport paid at once). Both fixed in #289.
- **A sibling's fee kept (526, 1 row).** An old 8540 EUR row belongs to the sister primary
  school. It is kept because the page still shows the number.
- **The agent's own errors (158, 4 rows):** fees from the sister nursery's price list on the
  same site. 158 publishes nothing either way, because the school is withheld as a shared site.
- **Duplicates (3):** published rows that repeat a fee already matched, with no period or no group.
- **Not reached:** 199's fee page is marked invalid by an earlier stage, so the agent skips it
  and the old 5 of 10 fees stay. 520's preschool fees and the 2 and 8-instalment plans at 170
  were not read.

Small sample: 13 schools with rows, 77 fees, 2 of them near what the fixes were tuned on.

## Re-run on the merged code (step 4b)

The review fixes (#286, #287, #289) changed behaviour, so the same 24 schools were run three
more times on merged main (2208893), each on a fresh copy of the launch DB ($0.025, $0.018 and
$0.018). Output is in `step4b/run`, `run2`, `run3`. Published rows that match a step 4
candidate (same fields, year and label) take its verdict (`step4b/inherit.py`); the 8 new rows
were judged by hand against the same key (`step4b/score.py`, result in `step4b/score.out`).

| After every gate | Today | Step 4 | Run 1 | Run 2 | Run 3 |
|---|---|---|---|---|---|
| Wrong | 18 % | 12 % | 13 % | 12 % | 12 % |
| Found (of 77) | 40 % | 68 % | 53 % | 57 % | 60 % |
| Found, without 170 (of 63) | | 62 % | 60 % | 60 % | 63 % |

Every run is better than today on both counts, and two of three pass the 55 % bar. The drop
from step 4 is 170: in all three runs the reader returned the same 5 of the 14 fees on the
page it read fully in step 4 (same pages, same text length, same reader prompt), so it is not
noise in one run. Why step 4's read was complete is not known. Five of the 24 schools gave a
different result between runs (151, 170, 183, 568, 625), so a single run moves "found" by
several points and one run cannot tell a small fix from noise.

These 24 schools have now been looked at and tuned on (the #289 fixes, and any fix for 170's
read), so they are no longer a fair final test. All 44 untouched Sofia schools are used up.

## With the second read (#290, step 4d)

#290 reads a page again when it still shows published amounts the first read left out, and
adds the fees at amounts the first read did not give. Two runs of the 24 schools on its head
(b8da715), each on a fresh copy of the launch DB ($0.027 and $0.026). Output is in `step4d/`;
new rows were judged by hand against the same key (`step4b/score.py`).

| After every gate | Merged main (3 runs) | #290 run 1 | #290 run 2 |
|---|---|---|---|
| Wrong | 12–13 % | 10 % | 10 % |
| Found (of 77) | 53–60 % | 69 % | 71 % |
| Found, without 170 (of 63) | 60–63 % | 65 % | 68 % |

The second read fired at 170 (4700, 5100 missed; 8 fees added in both runs), 520 (7300;
the preschool table added in run 2 only), 526 (8540, the sibling's fee; nothing added) and 568
(once). It added no wrong rows. 170 is the school the change was designed around, and all 24
schools have been tuned on, so this is a regression check, not a fresh validation.
