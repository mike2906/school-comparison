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

`cli website-pins [--apply]` (after geocoding) pins a hidden location on its own website's
map point only when Nominatim's nearest building there is in our town, on our street, with
exactly our house number. Sofia only for now: another city needs its write bounds and
local name (`CITY_NAMES`). The website itself is not trusted (wrong sites, other branches fail the check).
In Sofia (2026-10-14) about 1 in 5 private sites with a map point passed; most private
kindergartens have no free source that places them.

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
