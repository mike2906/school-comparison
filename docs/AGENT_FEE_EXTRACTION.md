# Agent fee extraction (Lane D R6)

> Plans here are starting points, not instructions: re-evaluate an item against current
> facts before building it (`AGENTS.md` → "Plans are starting points").

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
   - **Payment options.** One fee paid in parts is one fee, not one per instalment. The reader
     gives the price paid in one go as the fee and each way of paying in parts as a payment
     option (`total`, `payments`, `instalment`, `first_payment`, as printed). Code keeps an
     option only when the evidence prints its numbers, clears a count written only in words,
     and drops a "1 x" option (the fee itself). Options are stored in
     `pricing_context.payment_options` and shown under the fee ("Or in parts: 2 × 4,575 EUR
     (9,150 EUR in total)"); a "Plan 1"-style name is dropped from a fee that has them. Their
     amounts count as read, so the old rows of a school that stored each instalment as a fee
     give way to the one fee (596 had 42 rows: 3 fees and 6 grade bands × 3 plans).
   - **Fee names in English.** `plan_name` and `age_group` stay the site's own words; the
     reader also gives each in plain English (`plan_name_en`, `age_group_en`), stored in
     `pricing_context` and shown on the English page where we have no label of our own
     (625's "половин ден" showed as is). Two fees of one category, group and period always
     get the plan name that tells them apart, so a prepaid or promotional price no longer
     shows as a second bare "Tuition" (2026-10-15).
   - **Discounts.** A percentage or amount off (siblings, paying early, paying the whole year
     at once) is no fee: the reader lists it in `discounts` with a bg and an en sentence. Code
     keeps one only when its evidence line is on the page, holds its one number (percent or
     amount, never both), and both sentences state that number. A read that passed the
     thin-read guard stores the kept ones, or none, in `attributes.fee_discounts` (the first
     read's; a thin read leaves them alone); the detail page shows them under the fees, gated
     like `school_facts` and only while the school has a published fee.
   - **Instalments.** Where a site prints no price for paying in one go, instalments are
     still fees of their own. An amount the line gives as "1 x" ahead of "+ N x" is a first
     instalment: its period is cleared. A count of payments ("2 x", "на две вноски") states
     no period.
   - **Not fees** (2026-10-19): a fine, a late-payment or reminder charge, a duplicate document,
     a transcript or official note, and replacing a lost key or badge are no cost of attending.
     The reader skips them, and code drops one whose evidence or name says so (595's
     administrative price list showed "Глоба за тютюнопушене" as a registration fee).
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

5. **A second look (2026-10-17).** After the first read, a stronger model (`fee_check` tier,
   Sonnet 5.5 on Anthropic's API, `stages/fee_check.py`) reads the same pages and the read's
   passing fees, and lists fees the pages state that the read left out, and read fees that
   look wrong. Its missed amounts that the pages print join the second read's list (with the
   amounts of published rows the read left out), so whatever it adds still passes every check
   above: the checker never writes a fee. Wrong-looking fees go to the run report only. A
   failed or capped check leaves the read as it was. Why: parents' problems kept being found
   by Mike on the site (Maple Bear's extras, ranges, periods); in a trial on 15 keyed schools
   Sonnet flagged all 9 with real problems and 1 of 4 clean ones (Haiku was too noisy), but
   found only ~40% of missed fees, so it steers a re-read rather than scoring fees.
   `fees-agent --no-check` turns it off; with no `ANTHROPIC_API_KEY` it is off with a warning.
   - **Cost.** A first dry run (4 schools, 2026-10-19) cost about $0.047 a check, ten times the
     read. So a run with `--apply` records what each check saw (`attributes.fee_check`: the
     pages' hash and the fees read), and a school is checked again only when its pages change,
     the read gives a fee the last check did not see, or the last check's wrong fees did not
     reach GitHub: its cost follows how often fee pages change, not how many schools a run reads.
   - **Wrong fees reach a person.** A run with `--apply` lists every wrong-looking fee in one
     GitHub issue, "Fee check: fees that look wrong" (a comment when it is already open), with
     the gh CLI like the PC's other jobs. Once reported, a school is not flagged again until
     its pages or fees change, so the issue is the record.

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
costs about $0.20, inside the $1-a-day guide.

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
it comes after steps 1 to 5 prove the runs are reliable. Since 2026-10-20 (Mike's approval
of the schedule) `backend/scripts/refresh_fees.sh` re-reads and publishes the fees monthly from
cron on the PC, with the fee check and a $1.50 cap (`docs/SCHEDULED_JOBS.md`).

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
   to write). Since 2026-10-14 the finder also reads up to 4 pictures named for fees with
   `fee_image_reader.py` (594's tables are pictures), and a PDF's clean tables follow its text
   one row per line (596's PDF text interleaves table rows); scanned PDFs are not read yet.
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
   Re-run 2026-10-13 after #286 and #287 on the 24 untouched schools left
   (`docs/accuracy/step4_check.md`): **go**. Wrong 12 % after every gate against 18 % today,
   68 % of site fees found against 40 %. The remaining wrong rows are last year's fees kept as
   history, old rows the rule-6 keep holds (a sibling's, a number still on the page), and old
   rows an undated read did not replace (fixed in #289).
   That run was on the unmerged branch (22f2a85). On merged main (2208893) three runs found
   53 %, 57 % and 60 % (wrong 12-13 %): the reader gave 5 of 170's 14 fees in every run. With
   #290's second read, two runs found 69 % and 71 % (wrong 10 %). All 24 schools have now been
   tuned on, so these are regression checks, not a fresh validation.
   Mike chose to switch over on these numbers (2026-10-13). The extract stage no longer reads
   fees (`EXTRACTION_PIPELINE_PRICES=false` by default; `true` turns the old extraction back
   on, and `scripts/refresh_prices.py` needs it).
   Sofia run into the launch DB, 2026-10-13 (138 schools, $0.34, backup taken first): fee rows
   the API publishes 278 at 71 schools -> 587 at 85; site fees published on the reference set
   35 % -> 61 %. Published to production the same day (`sofia_schools-20261013-175611.dump`). 4 schools
   failed twice on a reply without its cost and kept their old rows. `accuracy_report.py` says
   21 % wrong, but 22 of its 28 wrong rows are instalments and printed new/returning totals
   (596, 506) that `reference_set.json` leaves out and `fresh_rescore/rules.md` counts as site
   fees. The 6 really wrong rows are old rows the read kept (506's learning-support fees as
   tuition, 538's last-year prices on an old page). A plan price also given as "1 x" the same
   amount (596) was stored twice; #292 stops that for later runs, and 596's rows stay doubled
   until it is re-read with `--apply` and published again.
4. **Delete the unused extraction code** that step 3 leaves behind (`extraction/price_*.py`,
   `fee_lines.py` and whatever only they import). Validation's `price_evidence.py`,
   `price_carry_over.py`, `fee_pages.py` and `fee_image_reader.py` stay unless nothing uses
   them. Big PR. Waits until Mike is sure of the agent's results.
5. **Schedule.** Add the monthly and summer-weekly run. Read several schools at once first:
   one at a time takes about 35 s a school (a Sofia run is over an hour), nearly all of it
   waiting on sites and the model.
6. **Publish automatically** behind the checks above, once Mike picks where the job runs.

## Not covered

- **Sites that block automated reading** (6 of 30 in the R4 sample). The agent uses the
  same crawler, so it is blocked the same way.
- **Fee tables published only as images** are covered only as well as
  `fee_image_reader.py` reads them today.
- **State schools.** They publish no fees, so nothing changes for them.
- **Kindergarten admission points** and other non-fee fields stay in the pipeline.
