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

## Rescore with a corrected key (2026-10-13)

The first pass below had three faults, found in a second-opinion review:
- Agent fees at schools where the key listed none were left out of the agent's accuracy, while
  published rows there were counted.
- The agent's 77 % was adjusted by hand to 93 to 95 %, then compared with an unadjusted 89 %.
- The old matcher counts a fee with no period as right, and ignores grade, plan and year.

So every row from both sides was judged again, the same way, against a key rebuilt from the
schools' pages. Everything is in `fresh_rescore/`, and
`python3 docs/accuracy/fresh_rescore/rescore.py` reproduces the numbers.

- **Rules** (`rules.md`). A site fee is one printed amount for one group, plan and year, so each
  instalment is its own fee. Period is how often that amount is charged: once per school year is
  yearly, once ever is one_time, a one-off first or last instalment has no period, and a deposit
  is a registration fee. A total the site does not print is not a fee.
- **Blind.** For each school, six Claude agents saw the agent's rows and today's published rows
  shuffled together and unlabelled (`packets/`). They fetched the pages again with WebFetch,
  listed every fee and gave each row one verdict (`adj/`). `hidden_map.json` says which row came
  from which side.
- **Evidence.** 515's fee tables load in tabs that WebFetch cannot see, so its tuition verdicts use
  the crawler's rendering of the same pages (`run/fresh_check.log.txt`, fetched the same day).
  Every other verdict is from a fresh fetch.
- **Fixes in the scoring script.** The adjudicators sometimes marked a row "duplicate" because the
  other side had already matched that fee. Such rows are judged again on their fields. Rows for
  2026/27 that were marked stale count as current. Fees from a 2025/26 table that the site also
  states for 2026/27 are left out (13, mostly at 305).
- **Audit.** A second agent judged 20 random verdicts blind and agreed on 18. Both
  disagreements were a deposit or admin fee with no stated period, where it was more lenient.
- **Raw outputs.** The original run outputs are in `run/`. The original key is unchanged in
  `reference_set_fresh.json`.

### Corrected results

The corrected key has 226 fees at 35 schools. The original had 149; the biggest gaps were 515's
school tables, 305, 171 and 634. 7 schools state no fees.

| | Agent | Published today |
| --- | --- | --- |
| Rows | 158 | 72 |
| Fully right | 116 (73 %) | 46 (64 %) |
| Right but period left blank | 29 (18 %) | 22 (31 %) |
| Wrong | 13 (8 %) | 3 (4 %) |
| Duplicate of its own row | 0 | 1 |
| Unresolved | 0 | 0 |
| Site fees found, period blank allowed | 143 of 226 (63 %) | 66 of 226 (29 %) |
| Site fees found fully right | 114 of 226 (50 %) | 44 of 226 (19 %) |
| Schools with fees where it has at least one usable fee | 33 of 35 | 24 of 35 |

Two caveats. 8 published rows and 2 agent rows have no label, so they could not be wrong on
group, plan or year. And published rows still pass validation before the site shows them, while
the agent's rows had no such check.

**The agent's 13 wrong rows:**
- **Period (11).**
  - Yearly fees filed as one_time (5): 220's 8330 and 150, 531's 390 twice, 632's 7000.
  - First instalments filed as monthly (4): 515's 2992 twice, 2904 and 2828.
  - A last instalment filed as quarterly (1): 220's 1540.
- **Category (1).** 220's 770 deposit filed as tuition.
- **Amount (1).** 547's 550 to 600 range, built from two monthly fees.

**Today's 3 wrong rows, plus 1 duplicate:**
- Two amounts that are on no page (381's 200 BGN, 532's 200 BGN).
- A yearly fee filed as one_time (601's 350).
- The duplicate repeats a row it already had.

**Where the agent misses:**
- 305: 18 current fees, with no priced page found.
- 515: 8 fees from the grades 5 to 12 table, on the same page as the kindergarten table it
  read, plus the 4 first instalments it filed wrong.
- 171: 11 deadline-tier fees.
- 634: 9 fees.

### Against the step-1 bar

The plan's bar is: "at least as accurate as today and finds at least 55 % of the site fees".
- **Fees found: passed.** 63 % with a blank period allowed; 50 % fully right.
- **Accuracy: mixed.**
  - The agent is fully right more often (73 % against 64 %), because today's rows leave the period
    blank twice as often.
  - The agent is wrong more often (8 % against 4 %, 13 rows against 3).
  - 12 of its 13 errors break the period and deposit rules above, which the prompt has not been
    told yet; the 13th is the range built from two fees.

So step 1 passes on fees found but not cleanly on accuracy. Step 2 is justified only with those
rules in the prompt and in code, and it should be checked on new schools before the switch-over
(see `docs/AGENT_FEE_EXTRACTION.md`). This sample is now development data: the step-2 prompt
fixes come from it.

## First pass (superseded by the rescore above)

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

## What the first pass concluded (corrected above)

- **Accuracy holds on unseen schools** once the key's own gaps are allowed for, and the
  agent finds almost twice the fees we publish. The rescore does not support the first half
  of this: the agent is wrong more often than today's rows.
- **The amount check catches nothing here.** It dropped one correct fee and no wrong ones.
  The agent's errors are in category and period, not invented numbers. The check stays as
  cheap insurance, but it is not what keeps accuracy up.
- **Step 2 should fix the clear errors.** The prompt needs to:
  - name instalment plans (first payment plus N × amount) and keep the period of each part;
  - give deposits a home;
  - never build a range out of two different fees.

  Discovery needs a fallback for sites like 305.
