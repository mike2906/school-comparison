---
name: geocoding
description: How BG school geocoding works — GeoJSON province/municipality naming (СТОЛИЧНА, not СОФИЯ), the GeoJSON→Nominatim provider tiers, and city-match rules. Use when touching anything under app/services/geocoding.
---

# Geocoding & GeoJSON Skill

Use this when working on geocoding — resolving school locations, the composite provider,
or the EU GeoJSON dataset.

## GeoJSON City Convention

The geocoding system uses the EU Commission's GeoJSON education dataset
(`backend/data/bg/education.geojson`).

**CRITICAL:** GeoJSON uses **province/municipality names**, not city names:
- Sofia schools: `city="СТОЛИЧНА"` (Stolichna = Capital municipality)
- NOT `city="СОФИЯ"` (Sofia city name)

**Impact on code:**
- Database stores: `city="sofia"` (lowercase ASCII)
- GeoJSON provider automatically maps: `"sofia"` → `"СТОЛИЧНА"` for matching
- City normalization handles: `sofia`, `SOFIA`, `София`, `СОФИЯ`, `СТОЛИЧНА` → all map to `"СТОЛИЧНА"`

**Location:** `backend/app/services/geocoding/bg/geojson.py:_normalize_city()`

## Geocoding Strategy

The composite provider uses a two-tier approach:
1. **GeoJSON lookup** (instant, no API calls) - tries to match by (school_name, city)
2. **Nominatim fallback** (OpenStreetMap API) - used when GeoJSON has no match

**City matching rules:**
- Exact match preferred: `("ДГ 5 НАДЕЖДА", "СТОЛИЧНА")`
- Fallback if unique: `("ДГ 5 НАДЕЖДА", any city)` - only if school name is unique across Bulgaria
- Ambiguous match rejected: If multiple cities have same school name, returns error with warning

## What gets a published pin

Only a building-level point is published. A location whose `geocode_meta.precision` is
`approximate` (a GeoJSON name match, a street without the house number) keeps its point in
the DB but the API sends no `lat`/`lng`: the school is listed with its address and sorts
last by distance (`SchoolLocationBase.withhold_unverified_pin`). EU GeoJSON name matches
were measured 10 m to 2 km off in Sofia (2026-10-14), so never treat them as exact.

Sofia state schools and kindergartens take the municipality's points
(`scripts/import_sofia_municipal_points.py`); a single-location school whose register
address is only its complex matches its one numbered point by name (`match_by_name`).
Kindergarten buildings the main layer lacks (сграда 2, 3) come from kg.sofia.bg's own map
layer (`Schools/SchoolsZones` layer 0), joined on our `source_esri_id=` tag (`match_by_id`;
only for hidden locations; the layer's name must carry our number and building, as its ids
are not always kg.sofia.bg's for the same kindergarten). arcgis.sofia.bg refuses non-browser User-Agents.

`cli register-pins [--apply]` (Sofia) pins a hidden location on Sofia Municipality's official
address register (ГИС-София open data, CC BY 4.0, credited on the About page; downloaded on each
run, `--register-file` for a local copy): street + exact house number, or complex/street + block
(+ entrance), in our town (с./гр. named in the address, else София and its кв.). Register points
for one address over 60 m apart (150 m for a block) are ambiguous unless our district settles
them (one plot's gates up to 150 m apart when the address itself names the district); "до бл."
matches nothing. Street names match without titles or a middle initial on one side ("Св.",
"Й."); a street missing from Sofia is looked for in the villages of a district the address
names (Лозен for "район Панчарево"), which then needs the site to agree on a far move. The school's own site wins (Mike, 2026-10-15): held back when
its website maps it only elsewhere, unless the site's text states our address (stray and old
maps are common); a move over 3 km needs our district or the site to agree; a point where an
unrelated school is already pinned is not taken (likely an old address). Run it first: it
has the house numbers OpenStreetMap lacks.

`cli site-address [--apply]` (Sofia, after register-pins) gives a school's one hidden location the
address its own site gives: the site's map (linked, or the coordinates its own scripts draw a map
with) within 40 m of a register point whose address the site's text names (Bulgarian or Latin), or
the address of the EU education map feature our hidden point came from when the site names it
too and the feature stands at it (Мечо Пух). Site text includes schema.org (JSON-LD) blocks; map
views wider than about 5 km or zoomed out below 15 are not points.
The location's address becomes the register's (old one kept in `geocode_meta.evidence`). Held
back when the site gives several addresses, when the address is already one of the school's shown
locations, when an unrelated school is pinned there, or when the school has two hidden locations.

`cli hand-pin --location-id N --lat --lng [--address] --note "..." [--apply]` pins one hidden
location by hand (manual_fix, old address and point kept in `manual_fix.previous`) when the
evidence is the school's own but no stage can read it. Run it as a named command, never as raw SQL.

`cli confirm-pins [--apply]` publishes a hidden point that already stands on our building:
Nominatim's nearest building there is our town, street and exact house number. Not when
the school's own website maps it only over 300 m away (the address may be stale).

A registry re-import (`discover`) recreates a school's locations, but keeps official and
hand-corrected points and, while the source still lists their address (or the address
site-address replaced), the exact pins these stages checked (`has_checked_pin`).

`cli website-pins [--apply]` (after geocoding) pins a hidden location on its own website's
map point only when Nominatim's nearest building there is in our town, on our street, with
exactly our house number. Sofia only for now: another city needs its write bounds and
local name (`CITY_NAMES`). The website itself is not trusted (wrong sites, other branches fail the check).
In Sofia (2026-10-14) about 1 in 5 private sites with a map point passed; most private
kindergartens have no free source that places them.

`cli pin-agent [--apply]` runs after it: one cheap model call per hidden location picks
its building from candidates sources gave (website map points with OSM's address there,
named OSM schools/kindergartens within 2 km, or up to 3 named as the school anywhere in the
city when none near our point is: the point may be a wrong geocode), never typing coordinates. Code then requires
the pick to be on our street (OSM tags, else Nominatim there), house number within 4 when
both are known, no other school pinned on the spot; a website point needs the number, an
OSM feature must be the only one named as the school, same kind, private or not alike (only
an abbreviation like ЧСУ/СУ or "частно" says which; one site mapped twice is one feature). Evidence goes in
`geocode_meta.evidence`; method `agent_pick`, tag `coords_source=pin_agent`.

**Weekly run.** `backend/scripts/refresh_pins.sh` runs both stages. It is not on cron by
itself: the monthly register refresh (`refresh_register.sh`, `docs/SCHEDULED_JOBS.md`) runs it
with `PINS_FORCE=1` after the import. On its own cron it would run hourly but only a week after the last good run: dry runs first, a stop
(GitHub issue "Pin refresh stopped", retried a day later) when either would place more
than `PINS_MAX_PLACED` (10); else backup, apply to the dry run's schools only, catchment
dry run (an issue "Catchment match changed" if it would change) and publish. About $0.04 a
week for Sofia (two agent passes). Setup, with the PATH cron lacks:

```
PATH=/home/mike2906/.local/bin:/usr/local/bin:/usr/bin:/bin
25 * * * * bash /home/mike2906/sofiaschoolcomparison/backend/scripts/refresh_pins.sh
```

Each good weekly run also runs `osm-check` (free, one Overpass query, writes nothing): schools
OSM tags with their MoE code (`ref:BG:neispuo`, about 3 in 4 in Sofia) whose published pin is
over 300 m from OSM's, whose school number differs, or whose website differs go to one rolling
issue "Schools that disagree with OpenStreetMap", after the week's pins are published, with its text replaced only when the list changes. Each is a
lead: fix from the school's site or an official source, never by copying OSM (ODbL).

The weekly run also runs `site-city` (free, no model): an accepted website whose home page
title, main heading or site name names another town ("гр.Варна" on a Sofia school's site,
2026-10-19) takes the URL-validation failure path, so its data leaves the site with that
run's publish and `recover-failed-urls` looks for the real site. When search does not find it,
`cli check-website --school-id N --url <site>` runs the same checks on a site you know (dry
run; `--apply` stores it only if valid). A school whose accepted site is wrong (587, a
kindergarten on its sibling school's drujba.org) takes `--replace --apply`: the old site's
pages, site-copied addresses and extraction are dropped as in the shared-site check's
replace, so re-run `navigate` and `extract`, then `school-facts` and `fees-agent`. More than `SITE_CITY_MAX`
(3) at once withholds none and opens the issue "Website town check stopped"; the pins go
ahead either way. URL validation applies the same rule to new sites
(`app/scrapers/site_city.py`).

It also runs `site-grades` (free, no model): a school's grade bands come from the register's
school type, which guesses (treaty schools default to 8-12, private "СУ" licences run higher
than what is taught). Grades the school's own published fees or facts name are added, and a
register band above the highest grade its facts state, that nothing names, is hidden (never
every band). The bands carry `age_group_source=site_grades:` / `age_group_hidden=site_grades:`
tags, so the ministry import keeps them and a withheld website takes them back
(`app/scrapers/stages/site_grades.py`). More than `SITE_GRADES_MAX` (10) changes at once
applies none and opens the issue "Grade band check stopped".

A new city needs its write bounds and its local name in `website_pins.CITY_NAMES`, then
one supervised run before the cron takes it (`PINS_CITY`).

## Reusing a point for another record (UF44)

- **Official building point first:** a location whose address gives the same street and house
  number as a location tagged `coords_source=sofia_municipal` reuses that point (method
  `official_point_same_address`), e.g. a private school renting floors in a state school.
  Address agreement only (`bg/address_match.same_building`), never name similarity.
- **GeoJSON name match, several locations:** a name identifies the institution, not the
  building. For a school with several locations the register address must be this
  location's and no sibling may sit within 50 m; otherwise the address tiers (Nominatim)
  are tried. A single-location school keeps the register point.
- `duplicate_geojson_name_match_different_address` is not terminal, so routine runs retry it.
- Nominatim never pins a neighbourhood/settlement centre or another numbered school or
  kindergarten (OSM amenity). Results are `exact` only when their house number (letter included) is the
  address's own № or street number, and the query carried it; never a block, a quarter
  number (Младост 4) or a postcode.
- `scripts/geocode_locations.py --check-shared-points` lists one school's locations on one
  point at different addresses (read-only).
