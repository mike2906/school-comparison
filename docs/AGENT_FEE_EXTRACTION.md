# Agent fee extraction (Lane D R6)

Decided with Mike on 2026-10-13: fees for private and international schools come from an
agent that browses each school's site and reads its fee pages whole, replacing the
price-extraction rules in the website pipeline. This note is the plan; nothing below is
built yet.

## Why

Measured on the R4 sample (24 readable schools, 164 fees stated on their sites; scripts on
branch `agent/second-reader-trial`, scored like `scripts/accuracy_report.py` but counting
`activities` and `extracurricular` as one category and dropping lines with no amount):

| | Fees right | Site fees found | Cost per school |
| --- | --- | --- | --- |
| Published today | 88 % of published rows (R4, strict scoring) | 35 % | |
| Agent, round 2 | 95 % (97 of 102) | 63 % (103 of 164) | $0.0009 |
| Agent, round 3 (a "list every row" prompt) | 80 % | 66 % | $0.0012 |

Round 2 is the version to build on. The rule-based price extraction is about 2,250 lines
in `app/scrapers/extraction/` (`price_lines.py`, `price_rows.py`, `price_step.py`,
`fee_lines.py`); 36 of 209 merged PRs in the month to 2026-10-12 touched price extraction
or validation. The agent replaces the extraction rules with two prompts.

Caveats:
- These numbers come from the schools the prompts were tuned on.
- The two rows are not scored alike. The 88 % uses R4's strict scoring, and merging
  `activities` with `extracurricular` flatters the agent. The 35 % and 63 % do use the
  same scoring.

Step 1 measures both sides the same way, on schools the agent has never seen.

## How it works

For one school, with its `website_url`:

1. **Find.** Render the home page with the crawler's browser (robots.txt respected), list
   its same-site links, and ask the cheap model (`cheap` tier) for up to 4 links likely to
   state fees. If it picks none, follow links whose URL or text names fees or admissions
   (`такс`, `цени`, `прием`, `fee`, `tuition`, ...). Up to 3 clicks deep, 12 pages. PDFs are
   read with pdfplumber.
   Pages whose fees are only an image go to the existing `fee_image_reader.py`.
2. **Read.** Send every page that shows a price to one `pricing`-tier call. Its output is the
   existing `ExtractedPrice` schema: category, amount or range, currency, period (null
   unless stated), `academic_year`, `age_group` and `plan_name` as the site states them, and
   the page each fee came from. Grade and group prices are what parents filter on.
   The prompt defines `activities` and `extracurricular` apart, and scoring keeps them
   apart from step 1 on.
3. **Check.** Keep a fee only if its amount is one of the numbers on the page it came from.
   The page's numbers are normalised for spaces, thousands separators and decimal commas
   (`7 400`, `1.200,00`), and phone numbers and dates are left out. This is a
   deterministic guard against invented numbers, not a second model.
4. **Store.** Upsert each page read as a `source_pages` row, and write the fees as `pricing`
   rows linked to their page: `source = scraped_website`, with `pricing_context` carrying
   `extractor: "agent"` and `confidence: 0.9` for fees that pass step 3.

The publish boundary does not change: rows go through `pricing_row_is_publishable`, the
validation stage's evidence check (`price_evidence.py`, which stays) and the
implausible-tuition check, so a page that turns invalid still withholds its fees. The
confidence floor (0.7) passes every agent row by design, so for these rows the guard
against invented fees is step 3. There is no new column, table or migration.

**Replacing rows.** This reuses today's supersede rule (`price_step._find_superseded_rows`):
a run replaces only the academic years it read, so earlier years stay as history. Because
the agent's reads vary from run to run, a school whose new read has fewer than half as many
fees as its current published rows keeps its old rows, and the run summary lists it. A
school where the agent reads nothing also keeps its rows. A failed or partial crawl must
not wipe good prices or make them flicker.

**Freshness.** Fees change about once a year, for the new school year. Run monthly, and
weekly from June to September. One run over Sofia's 175 private and international schools
costs about $0.20, inside the $0.50-a-run cap.

**Publishing without a person.** A run changes only the launch DB; production sees it
after a publish (AGENTS.md invariant 3). A scheduled run that waits for someone to publish
is a recurring human task, so step 6 makes publishing automatic, behind checks that hold
the publish and report instead:

- **A fresh check of a sample.** Each run, a stronger model re-reads the fee pages of 10
  random changed schools blind, and agrees with at least 90 % of the agent's fees there.
  This costs about $0.02. A fixed reference set is not used, because it goes stale every
  September and is the set the prompts were tuned on.
- **No large drops.** Published fee rows drop by no more than 10 % overall, and no school
  loses more than half of its published fees.
- **A safe upload.** The backup succeeds before the upload.

What blocks it today: the launch DB lives on Mike's PC, and the off-site backup there needs
him to run or allow it. So step 6 needs either a scheduled job on that PC that is allowed
to back up and publish, or the launch DB moved to a host that can. That is Mike's call, and
it comes after steps 1 to 5 prove the runs are reliable.

## Steps (one PR each)

1. **Fresh-sample check** (read-only). Done 2026-10-13: see `docs/accuracy/fresh_check.md`.
   Draw a new seeded sample of about 20 schools with fees, outside the R4 sample. Agents
   read the sites blind, as for R4. Then score the agent and today's published rows the same
   way, with strict categories. Go on only if the agent
   is at least as accurate as today and finds at least 55 % of the site fees. Otherwise stop
   and bring the numbers back.
2. **The stage.** `app/scrapers/stages/fees_agent.py`, plus a thin `fees-agent` CLI command,
   built from the trial scripts with steps 3 and 4 above. Tests use recorded pages and a
   stubbed model. Big PR (it changes what the API publishes): one `@codex review`.
3. **Switch over.** In one PR, stop the extract stage from calling `_extract_prices`, so a
   later pipeline run can't overwrite agent rows with rule-based ones. Then run the agent on
   Sofia into the launch DB, compare the scoreboard and `accuracy_report.py` before and
   after, and publish with a backup and before/after counts.
4. **Delete the unused extraction code** that step 3 leaves behind (`extraction/price_*.py`,
   `fee_lines.py` and whatever only they import). Validation's `price_evidence.py`,
   `price_carry_over.py`, `fee_pages.py` and `fee_image_reader.py` stay unless nothing uses
   them. Big PR.
5. **Schedule.** Add the monthly and summer-weekly run.
6. **Publish automatically** behind the checks above, once Mike picks where the job runs.

## Not covered

- **Sites that block automated reading** (6 of 30 in the R4 sample). The agent uses the
  same crawler, so it is blocked the same way.
- **Fee tables published only as images** are covered only as well as
  `fee_image_reader.py` reads them today.
- **State schools.** They publish no fees, so nothing changes for them.
- **Kindergarten admission points** and other non-fee fields stay in the pipeline.
