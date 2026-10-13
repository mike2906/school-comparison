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
   `extractor: "agent"` and a `confidence` that code sets, not the model.
   - The reader returns, for every fee, the verbatim page line (or table row and heading) it
     read the fee from. Code checks that the line is on the page and holds the amount; a fee
     without such a line is withheld.
   - **Period needs evidence.** Code keeps the model's period only when that line names it
     (`месеч`/month, `годиш`/`учебна година`/year, `тримесеч`/quarter, `срок`/term,
     `семест`/semester; for one_time, `еднократ`/once/`при записване`). Otherwise the period is
     cleared and the fee publishes with a blank period, as a third of today's rows already do.
     one_time is kept only for `registration` (enrolment, exam, deposit); on any other category
     it is cleared, because "еднократно плащане" of a year's tuition is still yearly.
   - **Instalments.** An amount the line gives as "1 x" ahead of "+ N x" is a first instalment:
     its period is cleared. A count of payments ("2 x", "на две вноски") states no period.
   - **Withheld** (confidence 0.5, under the 0.7 publish floor):
     - a fee whose line names a deposit (`депозит`, `капаро`, deposit) but whose category is
       not `registration`;
     - a range, unless the line joins its two ends with a range marker (`–`, `от … до`, to).
       Two fees in one table row are not a range.
   - **0.9** for everything else that passes step 3.

   The amount check in step 3 only guards against invented numbers. On the fresh sample, every
   agent error but one was a printed number with the wrong period or category, which that check
   cannot see. These rules are written to clear or withhold each of the 13 errors. Two of them
   (220's 1540, 569's 155) depend on what the page line says, so the step-2 tests check every
   case on the recorded pages, and that the valid fees next to them survive (see Steps).

The publish boundary does not change: rows go through `pricing_row_is_publishable`, the
validation stage's evidence check (`price_evidence.py`, which stays) and the
implausible-tuition check, so a page that turns invalid still withholds its fees. For agent
rows, the confidence floor (0.7) is how the holds in step 4 keep a fee off the site. There is
no new column, table or migration.

**Replacing rows.** This reuses today's supersede rule (`price_step._find_superseded_rows`):
a run replaces only the academic years it read, so earlier years stay as history. Because
the agent's reads vary from run to run, a school whose new read has, for the academic years
it replaces, fewer than half as many publishable fees as its current published rows keeps
its old rows, and the run summary lists it. A school where the agent reads nothing also
keeps its rows. A failed or partial crawl must not wipe good prices or make them flicker.

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
   The rescore there passes the fees-found bar (67 %) but is wrong more often than today
   (8 % against 4 %). The fresh sample is now development data.
   Draw a new seeded sample of about 20 schools with fees, outside the R4 sample. Agents
   read the sites blind, as for R4. Then score the agent and today's published rows the same
   way, with strict categories. Go on only if the agent
   is at least as accurate as today and finds at least 55 % of the site fees. Otherwise stop
   and bring the numbers back.
2. **The stage.** `app/scrapers/stages/fees_agent.py`, plus a thin `fees-agent` CLI command,
   built from the trial scripts with steps 3 and 4 above. The prompt takes the period and
   deposit rules from `docs/accuracy/fresh_rescore/rules.md`, and must never build a range
   out of two fees. Tests use recorded pages and a stubbed model. They include the 13 errors
   in `docs/accuracy/fresh_check.md` as regression cases, and valid fees from the same pages
   that the rules must not clear or withhold. Big PR (it changes what the API publishes):
   one `@codex review`. Experimental until step 3 passes: nothing calls it from `all`.
   Built 2026-10-13 as an experimental dry-run-by-default command (`fees-agent`, `--apply`
   to write). Not yet wired: fee tables published only as images (`fee_image_reader.py`).
   A dry run still calls the models and records its pipeline run and spend; it writes no fees or pages.
   `--apply` withholds the school's website data and runs Stage 6 on it straight after
   writing, as an extraction does; if Stage 6 fails the data stays withheld.
3. **Validate, then switch over.** First, about 20 schools from the 41 that neither sample
   used get a key read blind. The step-2 stage and today's rows are scored on them with
   `fresh_rescore/rules.md`, frozen as they are now. Score what would be published after every
   gate (`pricing_row_is_publishable`, evidence, implausible tuition), not the raw reader
   output. Go on only if the stage is wrong no more often than today's rows and finds at least
   55 % of the site fees. Then, in one PR, stop the extract stage
   from calling `_extract_prices`, so a later pipeline run can't overwrite agent rows with
   rule-based ones. Then run the agent on Sofia into the launch DB, compare the scoreboard
   and `accuracy_report.py` before and after, and publish with a backup and before/after
   counts.
   Ran 2026-10-13 (`docs/accuracy/step3_check.md`): **no-go**. Wrong 12 % after every gate
   against 14 % today (the agent's own rows 2 %), but only 15 % of site fees found: the
   replacement hold blocked 8 of 13 schools and the finder reached no fee page at 4. With nothing
   held the agent finds 42 %. Next step waits on Mike.
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
