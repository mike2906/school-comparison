# R6 step 1: agent fee extraction on fresh schools (2026-10-13)

The check `docs/AGENT_FEE_EXTRACTION.md` asks for before building: score the agent and
today's published prices the same way, on schools the agent was never tuned on.

## Sample and answer key

- `reference_set_fresh.json`: 50 schools drawn with seed 20261013 from the 138 Sofia
  private and international schools with a website. R4's schools were left out, along with
  any school sharing a domain with them, and only one school was taken per domain, leaving
  91 eligible.
- The key was read blind by five Claude agents with WebFetch on each school's own domain,
  with no access to the repository, DB or API. 8 sites blocked them, 9 state no fees, and
  33 state 149 fees.
- The agent run used scripts on branch `agent/second-reader-trial`: `fee_finder_trial.py`
  (commit 24e03f9) and `fee_amount_check.py` (5e26c57). It ran against the launch DB,
  read-only, and cost $0.063 for 42 schools ($0.0015 each).

## Scores, strict categories

| | Right | Site fees found |
| --- | --- | --- |
| Published today | 64 of 72 rows (89 %) | 67 of 149 (45 %) |
| Agent, as scored | 120 of 156 (77 %) | 122 of 149 (82 %) |
| Agent, after checking its 36 "wrong" fees against the pages | 145 to 149 of 156 (93 % to 95 %) | 82 % or more |

Every one of the 36 fees scored wrong has its amount printed on the school's page
(`fee_amount_check.py`). Checking them against the page text sorts them like this:

- **Real fees the key left out (16).**
  - The key reviewer saw no amounts at 515, but its admissions page (`/priem/`) has a full
    table by group and payment plan (13 of these fees are read right).
  - 517's 35 EUR exam fee, 329's 123 EUR theatre class and 574's deposit are also on their
    pages.
  - Outside the 156: 585, where the key found no fees, states 7 900 EUR tuition and a
    200 EUR application fee on its programme pages.
- **The same fee written differently (9).** 172's and 286's two-instalment amounts are read
  per instalment, while the key gives yearly totals, and 220's quarterly instalments are
  listed one by one.
- **Disputed period (4).** 220's 8 330 is "еднократно плащане" (one payment), and 531's
  and 601's resource fees are "еднократна такса" for the school year. Each is one_time to
  one reader and yearly to the other.
- **Clearly wrong (7).**
  - 515's first instalments (2 992 and similar, ×4) are filed as monthly. The page says
    1 × 2 992 + 8 × 1 051.
  - 547 has a registration fee of 550 to 600 that the reader built from the two tuition
    amounts.
  - 632's full-year 7 000 is filed as one_time, not yearly.
  - 220's deposit is filed as tuition. This one is borderline, since there is no deposit
    category.

The fees at 172, 220, 515, 547, 574 and 585 were checked against the page lines. Those at
286, 329, 517, 531, 601 and 632 were judged from the fees' labels and the reviewers' notes.

Today's published rows were scored against the same key, which has the gaps above, so
their 89 % may be a little low too.

## Where the agent misses

- **305 (12 of the 27 misses).** It opened 8 pages and none showed prices, so nothing was
  read. This is page discovery.
- **171 (4).** It missed the tuition steps tied to enrolment deadlines.
- **529 (4).** It missed the instalment totals.

## What this means for the build

- **Accuracy holds on unseen schools** once the key's own gaps are allowed for, and the
  agent finds almost twice the fees we publish.
- **The amount check catches nothing here.** It dropped one correct fee and no wrong ones.
  The agent's errors are in category and period, not invented numbers. The check stays as
  cheap insurance, but it is not what keeps accuracy up.
- **Step 2 should fix the clear errors.** The prompt needs to:
  - name instalment plans (first payment plus N × amount) and keep the period of each part;
  - give deposits a home;
  - never build a range out of two different fees.

  Discovery needs a fallback for sites like 305.
