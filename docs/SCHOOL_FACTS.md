# School facts agent

> Plans here are starting points, not instructions: re-evaluate an item against current
> facts before building it (`AGENTS.md` → "Plans are starting points").

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
   curriculum, school day, admissions, clubs, transport, meals, support pages; testimonials
   are skipped. Up to 2 admissions links (`ADMISSION_LINK_RE`: прием, кандидатстване,
   priem, admission) are always opened ahead of the picker's choices: on 2026-10-14, 27 of
   the 40 private Sofia schools with no admissions fact never had their admissions page
   read (172's picker took the pupils' testimonials over /kandidatstvane/).
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
none shows nothing. A fact about a summer school, camp or holiday programme is never `key`
(its hours or meals would read as the school year's), and a fact that reports a pupil's,
parent's or graduate's remark is not shown at all.

**Labels feed the existing filters and fields** (`publication_attributes`): a shown fact's
tags join `filter_tags` (so the search filters and the compare page's meals, transport and
after-school rows see them), its languages join `language_focus`, and its class size is
used when no curated one exists (before the old extraction's), unless the fact is about one
grade, intake, profile or course, or a summer programme: the fact still shows, but the
class-size tile is for the whole school. The response also carries
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

## Open days (`open-days` stage, run from cron)

Open days and application windows are announced weeks ahead, so a yearly facts read misses
most of them (launch DB, 2026-10-14: 12 schools had open-day dates, 2 of them upcoming).
The `open-days` stage (`app/scrapers/stages/open_days.py`, rules in
`app/scrapers/extraction/open_days.py`) checks every school with an accepted website often
and cheaply:

1. **Open, no model.** The home page, the pages it watched before, the pages stored date
   facts came from, up to 3 home-page links named for open days or admissions, and (only
   when a mention changed) up to 2 links on the changed lines, such as a headline's article.
   6 pages a school at most.
2. **Hash the mentions.** Lines naming an open day, open lesson, information meeting or
   application window (`MENTION_RE`), with 2 lines either side, cleaned like the quote
   check; numbers on a nearby line with no date (a visitor counter) are masked. A news
   feed elsewhere on the page changes nothing. Hashes live in
   `attributes["open_days_watch"]` (internal).
3. **Read only on a change.** One reader call on the lines around the changed mentions
   (today's date in the prompt; an open day without a year is the next one, and only within
   90 days; an application date needs its year on the page, since an undated one is
   usually last campaign's page), then the school-facts checks: quote on a page read, sign-up link on a page,
   and the judge. Past dates are dropped. A kept date fact is stored with the school's
   facts at least as `more` (the judge grades for choosing a school, and a `minor` fact
   would stay out of the dates at a glance). A page whose stored dates a full facts read
   did not carry is read again once.
4. **Remove.** A stored date fact whose page was read again in full without its quote or
   its sign-up link is removed (a moved sign-up link changes the hash, so it is read
   again). A page that redirects to another site is not read. A fact whose open days have
   all passed is not shown (`publishable_school_facts`), whichever stage stored it.

About $0.001 to $0.002 a school whose mentions changed; nothing otherwise. The first run
reads every school that mentions open days (estimated at $0.10 to $0.30 for Sofia; measured on the first run).

`backend/scripts/watch_open_days.sh` runs it from cron on the machine with the launch DB:
hourly, doing nothing until a check is due (a day after the last, a week in July and
August), so a laptop that slept catches up at the next hour. $0.30 a run and $0.60 a day
at most (`OPEN_DAYS_MAX_USD`, `OPEN_DAYS_DAY_USD`). It publishes with
`publish_snapshot.sh` only when some school's dates changed; a failed run or publish opens
one GitHub issue, "Open-days check stopped", and retries at the next hour; so does a run
in which more than 1 school in 10 failed. Log and reports:
`~/.local/state/schooldecider/open_days.log`, `open-days-*.json` (kept a month). Setup:

```
PATH=/home/mike2906/.local/bin:/usr/local/bin:/usr/bin:/bin
10 * * * * /home/mike2906/sofiaschoolcomparison/backend/scripts/watch_open_days.sh
```

Not covered: open days announced only on Facebook or Instagram (see `social-media/research.md`
in the project files), and pages robots.txt blocks.

## Website name (`website-name` stage) and brands in search

Parents search for a private school by the brand it writes in Latin letters (UWEKIND, Busy
School); our English name transliterated the register name ("Uvekind", "Bizi"), and the old
pipeline's display name needs two pieces of evidence, so most brands were missing
(2026-10-16). Two parts, separate from the facts read so that a name run never replaces
stored facts:

1. **Website name** (`app/scrapers/stages/website_name.py`, rules in
   `app/utils/website_name.py`). The home page only (title, site name, logo text, headings,
   opening text) and one `cheap`-tier call for the Latin name the site uses for this
   institution, about $0.001 a school. Code keeps it only when it is in Latin letters, on
   the page (as a phrase, or as one word: "questjunior" for Quest Junior, but not only
   inside an e-mail or web address), not the web address itself ("izgrevou.com",
   "Wonderhouse-bg", "Lincolnsofia"; also checked when shown), and a distinctive word of it
   is in the site's address or matches the register name the way search does (so "My
   Blog" or "Kidscool Site", a template's name, is dropped). First dry run, 2026-10-19:
   138 Sofia schools on Haiku, $0.04. Stored as `attributes["website_name"]`; a home page not read keeps the stored
   name, one read with no name kept clears it. Shown under the school-facts rules
   (`shown_website_name`: no withholding marker, accepted website, same site) as the English
   name when the old pipeline has no corroborated display name, and searched.
   `uv run python -m app.scrapers.cli website-name [--school-id N] [--apply] --out report.json`
   (private and international schools by default; dry run by default).
2. **Web address in search.** Many brands are written on the site only in the logo picture,
   so the address is their one Latin spelling (busy-school.com, questjunior.bg). Search
   matches the address words of an accepted website, ranked with addresses below every name
   match. No model, nothing shown.

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
`attributes["social_links"]`. Share buttons, groups, events and posts are not a page, nor is a council's (a district,
the municipality or the regional education office, named in the page address); a page
linked from two different schools' websites (a web designer's) is dropped. The detail page
links them; `app/utils/social_links.py` `shown_social_links` gates them like the facts. A
school with no website gets no link: website discovery keeps the pages search finds in
`attributes["social_link_candidates"]` (never shown; a name match is often another school's
or a fan page) until a check such as the page's phone or address against the registry
confirms one. Reading
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
