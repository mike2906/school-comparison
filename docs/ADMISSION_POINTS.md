# Admission points: rules, calculator and catchments

Design for the parent-facing admission calculator (kindergarten, first grade, eighth
grade) and the catchment map. Research and sources:
`/mnt/project-files/admission-points/research.md` (project files), summarised here only
where a decision depends on it.

## Decisions

1. **Rules are data plus a small explicit evaluator.** Each rule set is a JSON file of
   parameters (points, tiers, dates, sources, worked examples). Each family of rules has
   one evaluator written as plain code that reads those parameters. There is no generic
   rule language or criterion-block engine: Sofia's kindergarten rules, the national
   first-grade template and the eighth-grade score are different shapes, and a DSL would
   be harder to read and test than three short functions.
2. **Reuse follows the law, not the city.** First grade is one national template
   (Наредба 10, чл. 43): four residence groups, then municipal extra points inside a
   group. So `bg/grade1.js` serves every Bulgarian city; a new city is a new parameter
   file. Eighth grade is national: one evaluator, the per-class formula is data.
   Kindergarten rules are municipal and differ in shape, so each shape gets its own
   evaluator; when a second city with the same shape arrives (Varna, Veliko Tarnovo and
   Burgas use graded catchments), it gets a parameter file for a shared evaluator then.
3. **City-agnostic helpers live in `core.js`:** value ranges for unknown answers, address
   duration in years from dates, choice-rank points, rule-set selection by campaign,
   revision and status. They know nothing about Sofia.
4. **Keyed by campaign, then revision.** `campaign` is the school year the child starts
   (`2026-27`). Inside a campaign, `revisions` lists each act with its adoption and
   in-force dates, and `status_events` records suspensions (the April to May 2026 court
   suspension of the kindergarten address rules). A cut-off is read against
   `<rule set id>@<revision id>`, the same string the cut-off archive stores.
5. **Never roll rules forward.** When the parent asks about a campaign that has no rule
   set yet, the calculator uses the newest one and says plainly that the rules for that
   year are not published or not yet checked. Every rule set records when and against
   what it was last verified.
6. **Everything runs in the browser.** Answers include disability, deceased parents,
   adoption and foster care. They never leave the page: no API call, no URL parameter,
   no storage, no analytics event carries them. The backend serves no rules endpoint;
   rule files ship in the frontend bundle (loaded on the calculator route only).
7. **Unknown is an answer.** Every question has "not sure". The result is then a range
   (`8 to 12 points`) and the breakdown says which answer would settle it.
8. **Golden tests per rule set.** Each rule file carries worked examples, including
   boundary dates and mutually exclusive criteria; `rules.test.js` runs every example
   against its evaluator in CI.
9. **No promises.** Results show the source, the act and the date checked, link to the
   official system, and never say a child "would get in". Cut-offs are shown as "last
   year the last child admitted had N points", labelled with the rules they were made
   under; cut-offs made under different rules are marked as not directly comparable.

## Stages

| Stage | Who sets the rules | Calculator output |
|---|---|---|
| Nursery, kindergarten groups, preparatory groups (Sofia) | Municipal ordinance | General-queue points per kindergarten (district bonus and wish order depend on the kindergarten), plus social-queue and additional points |
| First grade (municipal schools) | National template plus municipal extra points | Residence group (1 to 4) per school, plus extra points inside the group |
| Fifth grade | No common points system: specialised schools admit on their own exams or НВО 4 | No calculator; the page says so |
| Eighth grade | National (Наредба 10, чл. 57 to 59) | Бал per class, from the class's own formula |

## Files

```
frontend/src/admission/
  core.js                         city-agnostic helpers
  registry.js                     every rule set, keyed by id
  evaluators/bg/sofiaKindergarten.js
  evaluators/bg/grade1.js
  evaluators/bg/grade8.js
  rules/bg/sofia/kindergarten/2026-27.json
  rules/bg/sofia/grade_1/2026-27.json
  rules/bg/grade_8/2026-27.json
  rules.test.js                   runs every rule file's examples
```

A rule file:

```json
{
  "id": "bg/sofia/kindergarten/2026-27",
  "country": "bg", "city": "sofia", "stage": "kindergarten", "campaign": "2026-27",
  "evaluator": "bg/sofia/kindergarten",
  "revisions": [{ "id": "2026-03-12", "act": "Решение № 200 / 12.03.2026", "in_force_from": "2026-03-19", "source": "decision-200" }],
  "status_events": [{ "status": "suspended", "from": "2026-04-..", "to": "2026-05-07", "scope": ["address"], "source": "..." }],
  "verified": { "on": "2026-10-14", "sources": ["decision-200"] },
  "sources": [{ "id": "decision-200", "title": "...", "url": "...", "retrieved": "2026-10-14" }],
  "official_system_url": "https://kg.sofia.bg/",
  "params": { "...": "evaluator-specific" },
  "examples": [{ "name": "...", "answers": {}, "context": {}, "expected": {} }]
}
```

## Cut-offs

Owned by the cut-off thread; shape agreed in
`/mnt/project-files/admission-points/cutoff-shape.md` and `cutoff-shape-comments.md`:
an archive of snapshots in `data/cutoffs/<country>/<city>/<stage>/<campaign>/<round>.json`
and a derived summary in `admission_info.cutoffs`, each row tagged with `rule_set`.

## Catchments and districts

Sofia publishes official first-grade catchment polygons (arcgis.sofia.bg, layer
`Schools/SchoolsZones` 2, 159 municipal schools), the neighbouring catchments of each
school (layer 1, `adjacentezonesid`), the 24 district polygons (layer 3), and the full
address list with each address's catchment school and neighbouring schools
(urbandata.sofia.bg, "Адреси и прилежащи училища СО", 106,629 addresses). All of them key
schools by the same GIS id, which kg.sofia.bg calls `esriId` and our kg.sofia.bg adapter
already stores as `attributes.kg_sofia_esri_id`.

- **Map.** A school's catchment is its own polygon (groups 1 to 3). Its neighbouring area
  is the union of the polygons of the schools that list it as neighbouring (+5 points,
  group 4 only). Two shades, not a scale. Each address belongs to exactly one catchment.
- **Address check.** A parent can type their street and number; the lookup runs in the
  browser against a compact index built from the address list, loaded only on demand.
- **Kindergartens** in Sofia have no catchment. The district bonus (3 points) depends on
  the kindergarten's district, which the page works out from its coordinates and the
  district polygons, and shows as an outline.
- **Ids.** The school response gains one public field, the catchment id (the GIS id),
  projected from `kg_sofia_esri_id` through the response allowlist.
- **Refresh.** A script rebuilds the static files from the two official sources
  (simplified on the server with ArcGIS `maxAllowableOffset`). The sources update once a
  year; the cloud cannot reach them, so the script runs in GitHub Actions or locally.

Rules change every one to three years per city and stage. A watcher (separate thread)
checks each rule file's `sources` and opens an issue or PR when the text changes; a
person or agent then updates the rule file and its examples.
