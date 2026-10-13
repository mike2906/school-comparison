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
| Published today | 88 % of published rows (R4) | 35 % | |
| Agent, round 2 | 95 % (97 of 102) | 63 % (103 of 164) | $0.0009 |
| Agent, round 3 (a "list every row" prompt) | 80 % | 66 % | $0.0012 |

Round 2 is the version to build on. The pipeline's price code is about 3,500 lines across
`app/scrapers/extraction/price_*.py`, `fee_lines.py` and `price_evidence.py`; 36 of 209
merged PRs in the month to 2026-10-12 touched it. The agent replaces most of that with
two prompts.

Caveat: these numbers come from the schools the prompts were tuned on. Step 1 checks them
on schools the agent has never seen.

## How it works

For one school, with its `website_url`:

1. **Find.** Render the home page with the crawler's browser (robots.txt respected), list
   its same-site links, and ask the cheap model (`cheap` tier) for up to 4 links likely to
   state fees. If it picks none, follow links whose URL or text names fees or admissions
   (`такс`, `цени`, `прием`, `fee`, `tuition`, ...). Up to 3 clicks deep, 12 pages. PDFs are
   read with pdfplumber.
2. **Read.** Send every page that shows a price to one `pricing`-tier call, which lists each
   fee with the site's own label, category, amount or range, currency and period (null
   unless stated).
3. **Check.** Keep a fee only if its amount appears in the page text it came from. This is
   a deterministic guard against invented numbers, not a second model.
4. **Store.** Upsert each page read as a `source_pages` row, and write the fees as `pricing`
   rows (`source = scraped_website`, `pricing_context.extractor = "agent"`, a fixed
   `confidence` of 0.9 for fees that pass step 3), linked to their page.

The publish boundary does not change: rows go through `pricing_row_is_publishable` and
the implausible-tuition check exactly as today, so a page that turns invalid still
withholds its fees. There is no new column, table or migration.

**Replacing rows.** A run replaces a school's scraped rows only when it read at least one
fee. A school where the agent reads nothing keeps its rows, and the run summary lists it:
a failed crawl must not wipe good prices.

**Freshness.** Fees change about once a year, for the new school year. Run monthly, and
weekly from June to September. One run over Sofia's 175 private and international schools
costs about $0.20, inside the $0.50-a-run cap. Production still needs a publish after a
run (AGENTS.md invariant 3); automating that is out of scope here.

## Steps (one PR each)

1. **Fresh-sample check** (read-only). Draw a new seeded sample of about 20 schools with
   fees, outside the R4 sample. Agents read the sites blind, as for R4, and the agent is
   scored against them. Go on only if it gets at least 90 % of its fees right and finds at
   least 55 %. Otherwise stop and bring the numbers back.
2. **The stage.** `app/scrapers/stages/fees_agent.py`, plus a thin `fees-agent` CLI command,
   built from the trial scripts with steps 3 and 4 above. Tests use recorded pages and a
   stubbed model. Big PR (it changes what the API publishes): one `@codex review`.
3. **Run it alongside.** Run it on Sofia into the launch DB in place of the pipeline's price
   step. Compare the scoreboard and `accuracy_report.py` before and after, then publish
   with backup and before/after counts.
4. **Retire the rule-based price extraction.** Stop calling `_extract_prices` from the
   extract stage, then delete the price-only code it leaves unused. Big PR.
5. **Schedule.** Add the monthly and summer-weekly run.

## Not covered

- **Sites that block automated reading** (6 of 30 in the R4 sample). The agent uses the
  same crawler, so it is blocked the same way.
- **State schools.** They publish no fees, so nothing changes for them.
- **Kindergarten admission points** and other non-fee fields stay in the pipeline.
