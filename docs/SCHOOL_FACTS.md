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
2. **Read.** One `pricing`-tier call lists up to 20 facts, each under one fixed topic
   (`app/utils/school_facts.py` `TOPICS`: curriculum, languages, school_day, class_size,
   meals, transport, activities, facilities, support, admissions), in Bulgarian and English,
   with the page line it came from. Fees, exam results, slogans, news and yearly counts are
   excluded in the prompt. A fact can carry labels its quote states: filter tags from the
   existing vocabulary (`FACT_TAGS`: transportation, cafeteria, meals_provided, extended_day,
   ...), languages with a level (immersion, bilingual, enrichment) and weekly hours,
   languages of instruction, a maximum class size, the admission route for a grade
   (`ADMISSION_TYPES`: entrance_exam, interview, trial_day, open_entry) with exam subjects
   and the grade or range of grades it is for,
   places full or waiting list for a grade, the highest grade taught, curricula
   (`CURRICULA`) and whether the diploma is recognised, application open/close dates, bus
   areas by district, special-needs support, and open-day dates with a sign-up link (kept only
   when code finds that link on a page read).
3. **Check.** Code keeps a fact only if its quote is on a page read (markup and quote marks
   ignored). A second call on the same tier confirms each quote states the whole fact and
   its labels, and grades it: `key` (what parents choose on: programme, languages, hours,
   bus, meals, class size, support, admissions), `more` (concrete but secondary) or `minor`.
4. **Store** (`--apply` only): `attributes["school_facts"]` =
   `{_schema_version, read_at, website_url, model, facts: [{topic, text_i18n, source_url, quote, rank, <labels>}]}`,
   key facts first. Stored facts whose quote is still on a page read are carried into the
   new set with their grade. A read that keeps facts replaces the stored ones, and so does a read of 4 or more pages
   that keeps none. A site not read, or read from fewer pages with no fact kept (often a
   partial crawl), keeps the stored facts.

**Shown to parents** (`school_facts` on the detail and compare responses; the quote stays
internal): a "From the school's website" box on the school page with the key facts (8 at
most, 2 a topic; later key facts show as `more`), a "show more" for the rest, a link to
each fact's page and the date it was read; and a compare-page section with one row per
topic any compared school has a key fact for. `minor` facts are not shown. A school with
none shows nothing.

**Labels feed the existing filters and fields** (`publication_attributes`): a shown fact's
tags join `filter_tags` (so the search filters and the compare page's meals, transport and
after-school rows see them), its languages join `language_focus`, and its class size is
used when no curated one exists (before the old extraction's). The response also carries
`school_facts.at_a_glance` (`at_a_glance` in `app/utils/school_facts.py`): admissions per
grade or range (a fact naming no grade stays its own row), places (only for 180 days after the read), the highest grade stated, curricula,
diploma, the first application window not yet closed, instruction languages, language
hours, bus areas, support and the next open days (until their date), summed from the shown (not minor) facts and shown above the
fact list and as compare rows. Facts carry their own checks, so they do not wait for a Stage 6 report, but a website-data withholding marker
(failed URL check, shared site) hides them and their labels, and so does a change of
website (host and path) or a website URL validation has not accepted (`pending`,
`failed_validate`, `no_official_website`; `SITE_ACCEPTED_STATUSES`). The stage reads only
accepted sites.

Running it: `uv run python -m app.scrapers.cli school-facts --school-id N [--school-id M]
[--city sofia --limit 20] [--apply] --out report.json`. Dry run by default. About $0.006 a
school with 8 facts (Sofia ~$3); up to 20 facts with labels is estimated at 15 to 25 % more,
to be measured on the trial schools. Nothing reaches the site until an `--apply` run is published.

## Before a Sofia-wide `--apply`

- Dry run 1 of the stage on the trial schools (2026-10-13): 33 of 41 facts kept, $0.057.
  Fixed after it: the judge dropped facts its quote did state (headings, listed steps),
  one 56,000-character staff page pushed later pages out of the read (now 15,000 a page),
  and vague facts ("sports activities are part of the programme") got through.
- Dry run 2: 40 of 45 kept, $0.064 for 8 schools. Fixed after it: a space before a comma
  on the page broke the quote match; one-word headings ("Таекуондо") were rejected as too
  short (now a short quote must be a whole line); one line split into three facts (now one
  fact per quote); the menu and footer repeated on every page used up the read limit, so
  later pages were cut (now left out); standard state subjects listed as facts.
- Dry run 3: 41 of 49 kept, $0.044 for 8 schools (menu stripping cut the cost by a
  third). Fixed after it: a list or table quote (shift times by grade) now matches row by
  row, up to 8 lines; the judge sees each quote's page address (a B2 level on
  /anglijski-ezik/ is about English); plans in the future tense are not facts; a fact
  names only what its quote names. Facts still vary between runs (383 kept 8, then 4).
- Dry run 4: 37 of 43 kept, $0.043; every page now fits the read. Fixed after it: most
  wrong drops were the reader adding a year or adjective the quote lacks, so the reader is
  told not to; a current-year timetable is not a "plan"; a link quoted without its address
  now matches. Reads vary from run to run (383 kept 1, 8, 4, 4), so a stored fact whose
  quote is still on a page read is carried over with no model call; new facts come first.
- Note for runs: the cost guard reserves $0.10 per call, so `--max-usd` needs about $0.10
  headroom above the expected spend.
- Top up OpenRouter (about $1.35 left on 2026-10-13).
- Checker model: Luna. Considered (2026-10-14) TypeSafe's Jev decision model on OpenRouter
  ($0.042 per million input tokens, typed yes/no and choice answers with probabilities): the
  checker costs about $0.0005 a school, so it saves pennies, and its accuracy on
  Bulgarian quote-supports-fact checks is untested. Worth a trial only at much larger scale.
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

1. **Filter fields.** Started (2026-10-14): fact labels feed the existing filter tags,
   language focus and class size; still to measure against the old extraction. Search filters (language focus, special programmes, facilities,
   teaching approach) and the compare page's yes/no rows (meals, transport, after-school
   care, accessibility) need values from a fixed vocabulary, not sentences. The API already
   computes filter tags from free text (`compute_filter_tags` with `canonical_tags` and the
   `FACILITY_VOCAB` / `PROGRAM_VOCAB` / `APPROACH_VOCAB` lists), so the first thing to try
   is running the kept facts' text through it, rather than asking the reader for tags.
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

**Contact details** need no model: `_extract_contact_info_deterministic` is pattern
matching, so it can run over the pages the finder already fetched (the contact page is
usually among them) instead of the old navigation. They must be exact, a wrong phone is
worse than none, and most are already stored (Sofia, 2026-10-13: phones for 208 of 214
state, 60 of 74 private, 296 of 329 kindergartens). So: same crawl, separate step, keep
the stored value unless the new one passes the same format checks.

**Social links** (2026-10-14): the `social-links` stage (`app/scrapers/stages/social_links.py`,
no model calls) reads the home page, and the contact page when the home page links neither
network, and stores the school's Facebook page and Instagram profile in
`attributes["social_links"]`. Share buttons, groups, events and posts are not a page; a page
linked from two different schools' websites (a web designer's) is dropped. The detail page
links them; `app/utils/social_links.py` `shown_social_links` gates them like the facts. A
school with no website is not covered: website discovery rejects Facebook results. Reading
posts (open days announced only on Facebook) is not built; options and the recommendation are
in the project's `social-media/research.md`.

**Bad website URLs** surface here: 22seu.org (school 395) is a parked domain that
redirects to a holding page. A read that finds only a redirect stub should send the
school back to URL validation rather than just reporting nothing read.

What parents see today (Sofia, 2026-10-13, through the API's gates): 7 schools have a
summary paragraph; website data is publishable for 173 of 214 state schools, 57 of 74
private and international, and 97 of 104 kindergartens with a website. So facts are new
content for nearly every school, not a replacement.

Suggested order: (1) facts on the site (this work); (2) filter fields in the facts reader,
measured on a reference set, then retire stages 4 and 5 and the summariser; (3) contacts
and display names; (4) delete the old fee extraction when Mike is confident.
