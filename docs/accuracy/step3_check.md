# R6 step 3: validation on untouched schools (2026-10-13)

The go/no-go check `docs/AGENT_FEE_EXTRACTION.md` sets before switching fee extraction to the
agent: on schools nobody tuned on, the published output after every gate must be wrong no more
often than today's rows and must find at least 55 % of the fees the sites state.

**Result: no-go.** The agent reads accurately (2 % wrong against today's 14 %), but what would
publish finds only 15 % of site fees, because the replacement hold blocks most schools and the
page finder misses four sites. Even with nothing held the agent finds 42 %.

## Sample, key and run

- `step3/sample.json`: 20 schools drawn with seed 20261014 from the 44 Sofia private and
  international schools that neither earlier sample used (R4's and step 1's schools and their
  domains left out, one school per domain).
- `step3/key/`: fees read blind from each school's own site by four Claude agents with WebFetch,
  no access to the repository, DB or API, using the frozen `fresh_rescore/rules.md`. 13 sites
  state fees, 6 state none, 1 (541) blocks robots and is left out.
- `step3/run/`: `fees-agent --apply` (main at 69f8389, which includes #284) on a copy of the
  launch DB on Mike's PC, one school at a time, `--max-usd 0.05` each. Total $0.052. `before.json`
  and `after.json` list each school's website price rows and whether the API publishes them.
  The launch DB was not written to (526 price rows before and after); the copy was dropped.
  The run needed `PROVIDER_REQUEST_RESERVE_USD=0.01`: with the default $0.10 reserve per request,
  a cap under $0.10 refuses to start.
- `step3/packets/`, `step3/adj/`, `step3/hidden_map.json`: three blind judges re-read the pages
  and gave every row one verdict under the frozen rules, without knowing which extractor it came
  from. They corrected the key where the page showed otherwise (marked `changed`).
- `python3 docs/accuracy/step3/score.py` reproduces the numbers below (`score.out`); pass `505` to
  leave out school 505, whose PDF alone states 114 fees (`score_without_505.out`). 552's and 583's
  key fees (6 and 20, after dropping the sibling institutions' fees) are counted as missed by every
  side, since no side had rows there to judge.

## Results

Three views of the same schools. "Found" counts site fees matched by a row that is right or
right apart from a blank period; "wrong" counts wrong amount, category, period, group or currency,
and stale rows. Duplicates (a second row for a fee already matched in the same view) are listed
apart.

| | Today (published) | Agent, nothing held | After every gate |
| --- | --- | --- | --- |
| Rows | 44 | 147 | 51 |
| Fully right | 29 (66 %) | 78 (61 %) | 33 (65 %) |
| Blank period | 9 | 46 | 12 |
| **Wrong** | **6 (14 %)** | **3 (2 %)** | **6 (12 %)** |
| Duplicates | 0 | 20 | 0 |
| **Site fees found (of 298)** | **38 (13 %)** | **124 (42 %)** | **45 (15 %)** |
| Found, fully right | 29 (10 %) | 78 (26 %) | 33 (11 %) |
| Found, without 505 (of 184) | 38 (21 %) | 93 (51 %) | 45 (24 %) |

Today's and the agent's rows together would find 136 (46 %).

## Why the published output finds so little

- **The replacement hold blocked 8 of the 13 schools that state fees** (`regressions` in each
  `run/agent_<id>.json`). Most holds protect fees the agent did not read: the two pre-school
  tuition fees at 300, four tuition tables at 505, 8100 and 9000 tuition at 593, the 400
  registration at 514. Three are false: 330 and 609 hold on old BGN rows the agent read in EUR, and
  533 holds to keep two wrong rows (each end of a "10-20 EUR" range stored on its own).
- **The finder reached no fee page at four sites that state fees** (52 fees): 535, 552 (an
  umbrella site linking the schools on another domain), 583, and 606 (fees served through the
  WordPress API, not in the rendered page). 541's link-picking call timed out.
- **Missed fees on pages it did read**: 505's long PDF (31 of 114), 514 (4 of 13), 593 (3 of 11).
- **Blank periods**: the evidence check clears a period the quoted line does not name, which
  cleared 46 periods that the page states in a heading or table header.
- **Duplicates**: 20 agent rows repeat a fee already read (11 at 564).

The agent's three wrong rows: €12 and €18 per-hour lessons filed as activities rather than
extracurricular (300), and a garbled "1/ден" catering line read as 1 EUR (514). Today's six: the
two ends of a range (533), a textbook fee and a BGN materials fee given a yearly period the site
does not state (606, 609), and two category errors (606, 609).
