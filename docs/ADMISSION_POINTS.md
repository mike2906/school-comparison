# Admission points: rules, calculator and catchments

Design for the parent-facing admission calculator (kindergarten, first grade, eighth
grade) and the catchment map. Research: `/mnt/project-files/admission-points/research.md`
(project files). Codex reviewed this design on 2026-10-14; its findings are folded in.

## Decisions

1. **Rules are data plus a small explicit evaluator.** Each rule set is a JSON file of
   parameters (points, tiers, dates, sources, worked examples). Each family of rules has
   one evaluator written as plain code that reads those parameters. There is no generic
   rule language or criterion-block engine: Sofia's kindergarten rules, the national
   first-grade template and the eighth-grade score are different shapes, and a DSL would
   be harder to read and test than short functions.
2. **Share what the law shares, no more.** First grade follows one national template
   (Наредба 10, чл. 43: four residence groups, then municipal extra points inside a
   group), so `bg/grade1.js` holds that grouping and a city adds a parameter file. Where
   a city's own procedure differs (address rules, locks, exceptions), it gets its own
   code beside the shared part rather than a growing list of flags. Kindergarten rules
   are municipal and differ in shape, so each shape gets its own evaluator; a second city
   with the same shape (Varna, Veliko Tarnovo and Burgas use graded catchments) shares an
   evaluator then, not before. Another country brings its own evaluators and result
   shapes; only `core.js` is shared.
3. **`core.js` is arithmetic, not policy:** ranges for unknown answers, calendar-safe
   durations (impossible, missing or future dates count as unknown; a 29 February start
   reaches its anniversary on 1 March), choice-rank points, and rule-set selection.
4. **One file is one immutable version of the calculation.** A file covers one campaign
   (the school year the child starts, spelled `2026/2027` like the rest of the repo) and
   holds the parameters in force for it, the acts it encodes (`acts`), its `version`
   (the date of the latest act) and dated legal events (`status_events`: `suspended`,
   `suspension_lifted`, `annulled_not_final`, `annulled_final`, `upheld_final`, never
   interchangeable). A change during a campaign adds a new file; old files never change
   meaning. Cut-offs store `<id>@<version>`, so each is read against the calculation it
   was made under, and cut-offs made under different rules are labelled as not directly
   comparable (Sofia's address points went from 5/4/3 to 8/5/3 in 2026).
5. **Never roll rules forward.** Each file says how far it applies: `verified` (checked
   for this campaign), `provisional` (written for this campaign from the law in force,
   before the campaign's schedule or amendments are out; re-checked when they are), or,
   when only an older campaign exists, an `illustration` that is never presented as this
   year's rules. Transitional provisions that end a criterion by campaign (the nursery
   point ends after 2027/2028) are parameters, so an illustration cannot award it later.
6. **Points, priority and capacity are separate results.** The kindergarten total sums
   every чл. 18 criterion and the wish-order points, as the published lists do. Social
   criteria also give first-ranking priority for up to 30% of places (чл. 14 ал. 5); SEN
   has 2 places per group plus a possible referral (ал. 6); chronic illness is capped at 3
   per group (ал. 9). Those are reported beside the total, not folded into it. First
   grade returns a group and points, never one number.
7. **Everything runs in the browser.** Answers include disability, deceased parents,
   adoption and foster care. They never leave the page: no API call, no URL parameter,
   no storage, no analytics or error-report payload carries them, and a typed address
   never moves the map (tile requests would reveal the area). The backend serves no
   rules endpoint; rule files ship in the calculator's own bundle chunk.
8. **Unknown is an answer.** Every question has "not sure". The result is then a range;
   its maximum assumes every unknown is favourable, so the page says "up to".
9. **Golden tests per rule file.** Each file carries worked examples, including exact
   anniversaries, mutually exclusive criteria, unknowns and invalid input;
   `rules.test.js` runs every example against its evaluator and checks the file's
   structure (ids, sources, version, status values).
10. **No promises.** Results show the source, the act and the date checked, link to the
    official system, and never say a child "would get in".

## Stages

| Stage | Who sets the rules | Calculator output |
|---|---|---|
| Nursery, kindergarten groups, preparatory groups (Sofia) | Municipal ordinance | Points per kindergarten (district bonus, sibling there and wish order depend on it), plus social priority and SEN/chronic places |
| First grade (municipal schools) | National template plus municipal extra points | Group (1 to 4) and extra points per school, for each address the parent could choose; the chosen address applies to every school and is locked after the first ranking |
| Fifth grade | No common system in Sofia; a national plan only for maths and science profiled schools (Наредба 10, чл. 73–74) | No calculator; the page says so |
| Eighth grade | National (Наредба 10, чл. 57–59) | Score per class from the class's own formula (weights, two grade subjects, aptitude exam, olympiad points) |

## Files

```
frontend/src/admission/
  core.js                               city-agnostic arithmetic and selection
  evaluators/index.js                   evaluator id -> module
  evaluators/bg/sofiaKindergarten.js
  evaluators/bg/grade1.js               national first-grade template
  evaluators/bg/grade8.js               national eighth-grade score
  rules/bg/sofia/kindergarten/2026-2027.json, 2027-2028.json
  rules/bg/sofia/grade_1/2026-2027.json, 2027-2028.json
  rules/bg/grade_8/2026-2027.json, 2027-2028.json   national (city: null)
  rules.test.js                         every file's structure and examples
  calculator.js                         tabs, start year, measuring date, cut-off rows
  ruleSets.js                           bundles every rule file (Vite only)
  catchments.js, loadCatchments.js      catchment and district lookups (static snapshot)
frontend/src/components/AdmissionPage/  /admission: one form per stage, results, rule notice
frontend/src/components/Admission/      school-page catchment map, address check, cut-offs
```

The page is `/admission?stage=kindergarten|grade_1|grade_8&school=<id>`. Only the
stage and the school id go in the URL; with a school, its district, catchment and
cut-offs fill in the school-specific questions. Address durations are counted to the
date the rules name: the ranking date for kindergartens (чл. 18 т. 1 "към датата на
класиране"), the application deadline for first grade. Eighth grade shows the score
under every formula the law allows, because each class's formula is in the school's
admission plan, which we do not have.

A rule file (abridged):

```json
{
  "id": "bg/sofia/kindergarten/2026-2027",
  "country": "bg", "city": "sofia", "stage": "kindergarten", "campaign": "2026/2027",
  "evaluator": "bg/sofia/kindergarten",
  "version": "2026-03-12",
  "acts": [{ "id": "2026-03-12", "act": "Решение № 200 ...", "in_force_from": "2026-03-19", "source": "decision-200" }],
  "status_events": [{ "status": "suspension_lifted", "from": "2026-05-07", "source": "vas-2026-05-07" }],
  "schedule": { "first_general_ranking": "2026-05-22" },
  "verified": { "status": "verified", "on": "2026-10-14", "sources": ["ordinance-consolidated"] },
  "sources": [{ "id": "decision-200", "title": "...", "url": "...", "retrieved": "2026-10-14" }],
  "params": { "...": "evaluator-specific" },
  "examples": [{ "name": "...", "answers": {}, "context": {}, "options": {}, "expected": {} }]
}
```

## Keeping rules current

Rules change every one to three years per city and stage, usually in February or March,
and sometimes by a court ruling. The watcher automates noticing, not rewriting: it never
edits or rolls a rule file forward, and the calculator keeps calling a provisional file
"not final" until someone confirms it.

**What runs.** `.github/workflows/watch-admission-rules.yml`, daily at 04:40 UTC on
GitHub's runners (they reach the Sofia sites; nothing runs on Mike's PC), runs
`backend/scripts/watch_admission_rules.py`. The targets and dates are in
`backend/app/scrapers/sources/bg/admission_rule_watch.json`; the logic is in
`app/services/admission_rule_watch.py`. It opens a GitHub issue labelled `rule-watch`
for each finding, or comments on the open one with the same title:

| Finding | When |
|---|---|
| A council session mentions admission rules | A new session page on council.sofia.bg names "прием на деца/ученици" (amendments, catchment changes) |
| A consultation mentions them | A new Sofia draft (sofia.bg public consultations) or national draft (strategy.bg: Наредба № 10, admission) |
| Municipal news | A new sofia.bg news title about admission or a court ruling on it (this is how the ВАС appeal on the first-grade address choice is followed; the court portals can't be read automatically) |
| The ИСОДЗ calendar changed | kg.sofia.bg's schedule for kindergartens and first grade (the next campaign's is due by 5 January) |
| No file for a campaign | On `next_campaign_by` (MM-DD of the year applications run), the family has no file for that campaign |
| A provisional file is not confirmed | On `confirm_by`, the campaign's file is still `provisional` |
| A source can't be read | Three runs in a row failed (HTTP error, robots.txt, layout changed: no links or too little text) |
| A listing may have missed entries | Every entry on a list page was new, so older ones may have scrolled to page 2 unread |
| The daily run failed | Checkout, install, posting or saving the state failed (scheduled runs only) |

Pages are compared as cleaned text (scripts and markup dropped, whitespace collapsed),
so a changed footer or session cookie doesn't count. List pages remember the links seen;
the first run only records them. A followed page is read again for 21 days (a session
page gains its decisions after the session) and reported again if its matching lines
change; a near-empty page (maintenance, bot check) is retried rather than marked read.
`also` makes a keyword count only on a line with admission words ("Наредба № 10" is
also an agriculture ordinance's number). The news target has none on purpose: a court
ruling's title may not mention admissions, and a missed ruling costs more than a
false issue. State (hashes, texts, seen links, failure counts) lives
on the `rule-watch-data` branch; deleting `state.json` there takes a fresh baseline.

**Acting on an issue.** Each issue the watch opens or comments on starts
`.github/workflows/draft-rule-update.yml`, where Claude (anthropics/claude-code-action, on
Mike's Claude subscription via the `CLAUDE_CODE_OAUTH_TOKEN` secret from
`claude setup-token`, valid a year) reads the issue and the source and either:
- closes the issue with a comment saying why nothing changed (or labels it `needs-human`
  when unsure), or
- opens a PR: a new rule file (or version) with its acts, sources and worked examples,
  `RULE_VERSIONS` updated when the version date changes (a test checks they agree with
  the rule files), or a schedule, status event or confirmation on the campaign's file.

Claude reads sources only through `backend/scripts/fetch_official.py`, which reads pages
and PDFs from the official hosts the rule files and watch config already use, so text on
a page cannot send it elsewhere. Its tools are limited to the issue, its own
`rule-watch/issue-N` branch, that script and the tests. Missing, provisional, unreadable
and failed-run issues are never closed without a fix.

It never merges: a PR is reviewed like any other, because a wrong points value is worse
than a late one. A catchment change needs a new snapshot
(`backend/scripts/build_sofia_catchments.py`). A date or unreadable-source issue closed
while its condition still holds is opened again the next day, on purpose. A provisional
file becomes verified only after the full official text was read. To redo a draft, run
the workflow by hand with the issue number. Without the secret the workflow only logs a
warning, and the issue waits for a person or agent.

**Adding a city or stage.** Add its rule family to `families` (a test fails until every
family under `frontend/src/admission/rules/<country>` has its dates) and its council,
consultation and schedule pages to `targets`. Another country gets its own
`sources/<country>/admission_rule_watch.json` and a `--country` run.

Known gaps: mon.bg is behind a Cloudflare check, so МОН drafts are seen through
strategy.bg, and the adopted text of a Наредба № 10 amendment is not watched (the
State Gazette isn't either); the 8th-grade file's yearly `confirm_by` re-check is the
backstop; the council page is per mandate, so its URL must change after the October
2027 elections (the watcher reports it as unreadable when it stops listing sessions).

## Cut-offs

Owned by the cut-off capture (`docs/ADMISSION_CUTOFFS.md`, PR #304): an archive of
snapshots per city, stage, campaign and round, and `admission_info.cutoffs` /
`cutoff_summary` in the API, each row tagged with `rule_set` (`<id>@<version>`).

## Catchments and districts

Sofia publishes official first-grade catchment polygons (arcgis.sofia.bg, layer
`Schools/SchoolsZones` 2, 159 municipal schools), the neighbouring catchments of each
school (layer 1, `adjacentezonesid`), the 24 district polygons (layer 3), and the full
address list with each address's catchment school and neighbouring schools
(urbandata.sofia.bg, "Адреси и прилежащи училища СО", 106,629 addresses). All of them key
schools by the same GIS school id (`integrationid` on layer 1). That is not the kg.sofia.bg
`esriId`, which numbers kindergartens in their own layer.

- **Classification uses the address table; the map only displays.** The membership of
  an address comes from the official address list, keyed by settlement code + street
  code + number (+ entrance), never from a point-in-polygon test. Polygons are simplified
  for display. Both are snapshotted per campaign (`catchments/bg/sofia/2026-2027/`).
- **Map.** A school's catchment is its own polygon (groups 1 to 3). Its neighbouring
  area is the union of the polygons of the schools that list it as neighbouring (+5
  points, group 4 only); adjacency is not always symmetric, so it is always read in that
  direction. Two shades, not a scale. Each address belongs to exactly one catchment.
- **Address check.** A parent picks their street and number; the lookup runs in the
  browser against a compact index built from the address list, loaded only on demand,
  and the map is not moved to the address.
- **Kindergartens** in Sofia have no catchment. The district bonus (3 points) depends on
  the kindergarten's district: the one kg.sofia.bg gives (`admission_info.admin_district`,
  from the cut-off capture), else the district polygon its coordinates fall in. The page
  shows the district outline.
- **Ids.** Schools carry `admission_info.catchment_ids: {"bg-sofia-gis": [...]}`, a list
  because merged buildings can have several, namespaced by provider so other cities
  can add their own. `backend/scripts/match_sofia_catchments.py` writes them, matching
  schools to the layer-1 points that have a catchment by number, type and address (the
  rules of `import_sofia_municipal_points.py`); run it after a new snapshot, then publish.
- **Refresh.** A script rebuilds the snapshot from the two official sources
  (simplified on the server with ArcGIS `maxAllowableOffset`). The sources update once a
  year; the cloud cannot reach them, so the script runs in GitHub Actions or locally.
