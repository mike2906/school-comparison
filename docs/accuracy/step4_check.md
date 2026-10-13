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

- **Old rows kept from a previous year (520, 4 rows).** The read missed the preschool tuition,
  so rule 6 kept the old rows at those amounts. Three of them are 2025-2026 fees the site has
  since replaced. The fourth, 70 EUR, is on no current page. Rule 6 only asks whether the page
  still shows the number, and 2025-26 and 2026-27 tables share a page.
- **One-time on a single payment (170, 2 rows).** "Еднократно плащане" for a year's tuition
  or transport means yearly. The agent read both with no period; Stage 6 filled one_time into the
  new 2000 EUR transport row, and the old 4500 EUR row with the same error was kept.
- **A sibling's fee kept (526, 1 row).** An old 8540 EUR row belongs to the sister primary
  school. It is kept because the page still shows the number.
- **The agent's own errors (158, 4 rows):** fees from the sister nursery's price list on the
  same site. 158 publishes nothing either way, because the school is withheld as a shared site.
- **Duplicates (3):** published rows that repeat a fee already matched, with no period or no group.
- **Not reached:** 199's fee page is marked invalid by an earlier stage, so the agent skips it
  and the old 5 of 10 fees stay. 520's preschool fees and the 2 and 8-instalment plans at 170
  were not read.

Small sample: 13 schools with rows, 77 fees, 2 of them near what the fixes were tuned on.
