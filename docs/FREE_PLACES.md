# Free kindergarten places

> Plans here are starting points, not instructions: re-evaluate an item against current
> facts before building it (`AGENTS.md` → "Plans are starting points").

Today's free places in every Sofia municipal kindergarten, nursery group, school
preparatory group (ПГУ) and municipal school's 1st grade, per building and birth year, as kg.sofia.bg (ИСОДЗ) shows them.
A parent looking for a place during the school year sees where one is free now; the
calculator says how many are free in the child's group at the chosen kindergarten.

## Source

`GET https://kg.sofia.bg/api/public/0/dz/{id}` (the record behind a kindergarten's page on
kg.sofia.bg) lists the building's groups with `spots` per queue (`general`, `sop`,
`chronic`): `capacity`, `zapisani` (enrolled), `svobodni` (free) and `nezapisani` (ranked
in a round, not yet enrolled: the place is held for them until the enrolment deadline).
-1 means the queue does not apply. These are the same counts as the site's own "Свободни
места" view (`kg.sofia.bg/spots`, whose `public/free/spots` endpoint only totals them per
district). `currentGroups` are this school year's groups, `featureGroups` next year's when
ИСОДЗ opens them in the spring. Institution lists: `public/kg/type/{kinderGarden|preparative|school}/all`;
the ПГУ list repeats every kindergarten with a preparatory group, so those are read once.
1st grade comes from `public/1/dz/{id}`, whose ids are a separate series (one can equal a
kindergarten's). Grades 2-12 have no city feed: schools and РУО post their own lists.

kg.sofia.bg publishes these counts openly and states no terms of use; they are counts
only, nothing about any child. We already capture its ranking lists the same way
(`docs/ADMISSION_CUTOFFS.md`). Private kindergartens are not in ИСОДЗ.

## Capture

`.github/workflows/capture-free-places.yml` runs `backend/scripts/capture_isodz_free_places.py`
at 04:20 and 13:20 UTC (morning and afternoon in Sofia): about 570 requests at two a second,
about 8 minutes. It writes one snapshot, `bg/sofia/free-places.json`, to the public
**`free-places-data` branch** (each commit replaces it; git history is the archive).
A scan with no buildings, more than one record in five failing, or fewer than 80% of the
last snapshot's buildings writes nothing and fails the run. A failed scheduled run opens
(or comments on) the issue "Free places capture failing".

Snapshot: `meta` (`source`, `source_url`, `retrieved_at`, `as_of` = Sofia date,
`school_years`) and `buildings`, each `source_id` (ИСОДЗ id), `family` (`kindergarten`,
`preparatory` or `grade_1`), `name`, `district` and `groups`: `group_id`, `label`, `birth_year`,
`campaign` (`2026/2027`), `stage` and `age_group` (as in the cut-off archive), `groups`
(number of groups) and `queues` (`general` / `sop` / `chronic` → `capacity`, `enrolled`,
`free`, `ranked_not_enrolled`; a queue with no capacity is left out).

## Into the site

No launch-DB write and no publish: the numbers change daily, while a publish replaces the
whole DB. The API reads the snapshot from the branch (`free_places_url` in
`app/config.py`, with an ETag so an unchanged file is not downloaded again) and serves
`GET /schools/free-places?country_code=bg&city=sofia`:
`{as_of, retrieved_at, source_url, schools: {"<school id>": [entry, ...]}}`. An entry has
`birth_year`, `campaign`, `stage`, `age_group`, `label`, `building` (only when the school
has several buildings), the general queue's `capacity`, `enrolled`, `free` and
`ranked_not_enrolled`, and `free_sop` / `free_chronic` when those queues have a place.
It is rebuilt every 10 minutes like the list, and served from the edge cache for 5.

Buildings match schools as in the cut-off import: a kindergarten by its kg.sofia.bg id in
`attributes.kg_sofia_id(s)`, a school's ПГУ by that id or else the Sofia school number
(`isodz_school_match.py`), and 1st grade by the school number only. On 2026-10-23 every building matched a school.

**Stale data shows nothing.** A snapshot older than 48 hours (four missed captures) gives
an empty `schools`, and the site also hides an answer older than that.

## On the site

- **School page** (municipal kindergartens, and schools with ПГУ or 1st grade): "Free places today", one
  line per group and building with the free places, capacity, enrolled and ranked but not
  yet enrolled, and the СОП and chronic-illness places.
- **Search list**: with a birth year and the current school year picked, each card says how
  many places are free for that birth year today (or that none is), and "Only with a free
  place today (N)" keeps just the schools with one (`free_places=true` in the URL; hidden,
  and ignored, for a school year with no snapshot yet).
- **Kindergarten calculator**: for the chosen kindergarten and start year, the free places
  in the child's group beside the cut-off comparison.

Live flows: `frontend/e2e/live/free-places.test.js`.
