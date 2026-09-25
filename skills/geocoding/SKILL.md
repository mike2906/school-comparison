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
- Nominatim results are `exact` only when their house number is one the query asked for.
- `scripts/geocode_locations.py --check-shared-points` lists one school's locations on one
  point at different addresses (read-only).
