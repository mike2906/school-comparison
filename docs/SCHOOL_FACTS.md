# School facts agent

Started 2026-10-13 with Mike. Replaces the never-published summary paragraph with concrete
facts read from each school's own pages, and is the first step towards reading every
website field with agents instead of the rule-based extraction (see "Retiring the website
reading pipeline" below).

## Why

The old summariser (`app/ai/summariser.py`) came out generic by design: it was fed
second-hand lists from the old extraction (5 items at most, "generic" phrases filtered),
its prompt banned numbers, years, class sizes and hours, regex checks rejected specific
claims, and failures fell back to a template. `publish_summaries` was never turned on.

Trial (`docs/accuracy/facts_trial/`, branch `agent/school-facts-trial`): 5 state and 5
private Sofia schools, read only, $0.06 in all. 8 of 10 got concrete facts with the page
line behind each; the two misses were a parked domain (22seu.org) and a robots.txt block
(81su.eu). Weak points found: a quote being on the page did not prove it states the fact,
some picks were trivia, yearly counts go stale, the paragraph only restated the facts, and
37 different free-text topic labels across 10 schools.

## How it works (`school-facts` stage, not part of `all`)

1. **Find.** The fees agent's finder (`find_pages`) with its own link prompt: about,
   curriculum, school day, admissions, clubs, transport, meals, support pages.
2. **Read.** One `pricing`-tier call lists up to 8 facts, at most 2 a topic, each under one
   fixed topic (`app/utils/school_facts.py` `TOPICS`: curriculum, languages, school_day,
   class_size, meals, transport, activities, facilities, support, admissions), in Bulgarian
   and English, with the page line it came from. Fees, exam results, slogans, news and
   yearly counts are excluded in the prompt.
3. **Check.** Code keeps a fact only if its quote is on a page read (markup and quote marks
   ignored). A second call on the same tier confirms each quote states the whole fact.
4. **Store** (`--apply` only): `attributes["school_facts"]` =
   `{_schema_version, read_at, website_url, model, facts: [{topic, text_i18n, source_url, quote}]}`.
   A read that keeps facts replaces the stored ones. A site not read, or read with no fact
   kept (often a partial crawl), keeps the stored facts.

**Shown to parents** (`school_facts` on the detail and compare responses; the quote stays
internal): a "From the school's website" box on the school page, one line per fact with
its topic and a link to its page, and the date it was read; and a compare-page section
with one row per topic any compared school has. A school with none shows nothing. Facts
carry their own checks, so they do not wait for a Stage 6 report, but a website-data
withholding marker (failed URL check, shared site) hides them, and so does a change of
website: facts show only while `website_url` is the site they were read from.

Running it: `uv run python -m app.scrapers.cli school-facts --school-id N [--school-id M]
[--city sofia --limit 20] [--apply] --out report.json`. Dry run by default. About $0.006 a
school (Sofia ~$3). Nothing reaches the site until an `--apply` run is published.

## Before a Sofia-wide `--apply`

- Re-run the 10 trial schools as a dry run with the stage and read the report.
- Top up OpenRouter (about $1.35 left on 2026-10-13).
- Decide refresh: facts carry `read_at`; a yearly re-read is enough for most topics.
  Runs vary, so a refresh could keep the old fact where the new read only rewords it.

## Retiring the website reading pipeline

Mike asked (2026-10-13) what else the pipeline does before going agent-only. Agents should
take over **reading** websites; the rest stays.

| Part | Agent replaces it? |
| --- | --- |
| Registry discovery, website discovery, URL validation, shared-site and campus checks | No. Agents start from `website_url`; a wrong URL gives confident facts about the wrong school |
| Navigation (stage 4, `navigator.py`) | Yes: both agents find their own pages |
| Fee extraction | Done (fees agent); old code kept until Mike is confident (`EXTRACTION_PIPELINE_PRICES`) |
| Other extraction (stage 5): languages, facilities, programmes, teaching approach, admissions, school day, meals, transport, support, class size, founding year | Yes, once the gaps below are closed |
| Contact details (`contact_details.py`) and display names (`display_naming.py`) | Later, separately (see below) |
| Stage 6 validation and publish gates | No: agents keep their own checks in code and still go through the gates |
| Summary (stage 7, `summarize`, `summary_pipeline.py`) | Yes: facts replace it; delete once facts are published |
| NVO and ДЗИ imports, geocoding, repairs | No |

Gaps to close before deleting stage 4 and 5 code:

1. **Filter fields.** Search filters (language focus, special programmes, facilities,
   teaching approach) and the compare page's yes/no rows (meals, transport, after-school
   care, accessibility) need values from a fixed vocabulary, not sentences. The facts
   reader should also return these from the existing vocabulary, each tied to a kept fact.
2. **Measure it.** Fees switched only after a blind reference set (found 68 % vs 40 %).
   Facts and filter fields need the same: a small hand-checked set of schools scored
   old-vs-agent for found, wrong and missed. The quote checks stop invented facts, not
   misread or missed ones.
3. **State schools.** Most of the ~500 schools are state schools; their sites are often
   thinner and more often blocked. Score them separately.
4. **Kindergartens** were not in the trial; their admission points come from kg.sofia.bg
   and stay in the pipeline either way.
5. **Failed reads keep data.** Done for facts (a site not read keeps its facts); the
   filter fields need the same rule.
6. **Blocked sites stay blocked** (robots.txt is respected): about 1 in 5 in the fee sample.

**Contact details** are a separate, small job, not the facts reader: they must be exact
(a wrong phone is worse than none), are checkable by format, mostly sit on one contact
page, and the registry already supplies many. Keep `contact_details.py` until a small
contacts read is measured against it.

Suggested order: (1) facts on the site (this work); (2) filter fields in the facts reader,
measured on a reference set, then retire stages 4 and 5 and the summariser; (3) contacts
and display names; (4) delete the old fee extraction when Mike is confident.
