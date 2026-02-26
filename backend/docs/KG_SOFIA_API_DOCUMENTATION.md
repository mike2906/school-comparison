# kg.sofia.bg API Documentation

## Overview
The Sofia Municipality provides a public API at `https://kg.sofia.bg/api/public` for accessing kindergartens and schools with preparatory groups in Sofia.

## Base URL
```
https://kg.sofia.bg/api/public
```

## Endpoints

### 1. Get All Regions (Districts)
**Endpoint:** `GET /regions/all`

**Response:**
```json
[
  {"id": "0", "label": "всички"},
  {"id": "01", "label": "Средец"},
  {"id": "02", "label": "Красно село"},
  ...
]
```

### 2. Get Kindergartens
**Endpoint:** `GET /kg/type/kinderGarden/all?filterType=by_region&kgType=0&regionId=0`

**Parameters:**
- `filterType`: `by_region` (default filter type)
- `kgType`: `0` (all types)
- `regionId`: `0` (all regions) or specific region ID (e.g., `01` for Средец)

**Response:**
```json
{
  "content": null,
  "error": null,
  "itemCount": null,
  "items": {
    "regions": [...],
    "kinderGardens": [
      {
        "id": 171,
        "name": {
          "publicType": "ДГ (с яслени групи)",
          "publicName": "ДГ №1 Червената шапчица (с яслени групи)"
        },
        "nameStr": "ДГ №1 Червената шапчица (с яслени групи)",
        "address": "гр. София, ул. \"Брегалница\", №48",
        "phone": null,
        "region": "Възраждане",
        "regionObj": null,
        "directorName": null,
        "directorFamily": null,
        "contacts": [
          {
            "id": null,
            "typeCommunication": {
              "id": 2,
              "komunikacias": [],
              "label": "business"
            },
            "kindCommunication": {
              "id": 2,
              "komunikacias": [],
              "label": "phone"
            },
            "fieldValue": "02/831 70 17"
          }
        ],
        "addressStructured": null,
        "studyMode": null,
        "esriId": 171
      }
    ]
  }
}
```

**Total:** 343 kindergartens

### 3. Get Schools (with preparatory groups)
**Endpoint:** `GET /kg/type/school/all?filterType=by_region&kgType=0&regionId=0`

Same parameters and structure as kindergartens endpoint.

**Total:** 158 schools

### 4. Get Preparatory Groups
**Endpoint:** `GET /kg/type/preparative/all?filterType=by_region&kgType=0&regionId=0`

Same parameters and structure as kindergartens endpoint.

**Total:** 321 institutions with preparatory groups

## Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Internal database ID |
| `nameStr` | string | Full name (use this) |
| `name.publicType` | string | Institution type abbreviation (ДГ, СУ, etc.) |
| `name.publicName` | string | Full public name |
| `address` | string | Full address string |
| `region` | string | District/region name (human-readable) |
| `contacts[]` | array | Contact information (phone, email, etc.) |
| `contacts[].fieldValue` | string | Actual contact value (phone number, email) |
| `contacts[].kindCommunication.label` | string | Type: "phone", "email", "fax" |
| `contacts[].typeCommunication.label` | string | Context: "business", "personal" |
| `esriId` | integer | ESRI GIS ID (for mapping systems) |

## Institution Type Codes (publicType)

| Code | Bulgarian | English | Our Schema |
|------|-----------|---------|------------|
| ДГ | Детска градина | Kindergarten | "kindergarten" |
| ДГ (с яслени групи) | Детска градина (с яслени групи) | Kindergarten (with nursery) | "kindergarten" |
| СУ | Средно училище | Secondary school | "upper_secondary" |
| ОУ | Основно училище | Basic school | "lower_secondary" |
| НУ | Начално училище | Primary school | "primary" |

## What the API Provides

✅ School/kindergarten name
✅ Full address string
✅ District/region name (human-readable!)
✅ Phone number (in contacts array)
✅ ESRI ID for GIS integration

## What the API Does NOT Provide

❌ Institutional ID (Код по НЕИСПУО) - must match with MoE data
❌ GPS coordinates (lat/lng) - need geocoding
❌ Age groups served
❌ Admission points thresholds
❌ Shift information
❌ School type (state/private)
❌ Website URLs

## Data Quality Notes

1. **Phone numbers** are in `contacts[]` array, not in `phone` field (which is always null)
2. **Region names** are standardized district names
3. **Address format** is consistent: `гр. София, ул. "Street Name", №Number`
4. **esriId** can be used for GIS mapping if needed
5. **No institutional IDs** - must match with MoE Registry by name

## Integration Strategy

### Recommended Approach:
1. **MoE API** → Get all schools/kindergartens with institutional IDs
2. **kg.sofia.bg API** → Enrich with addresses, phone numbers, districts
3. **Match by name** → Fuzzy matching on `nameStr` vs MoE `name`
4. **Geocoding** → Convert addresses to lat/lng (Google Maps API or Nominatim)

### Matching Algorithm:
```python
from fuzzywuzzy import fuzz

def match_schools(moe_school, kg_school):
    # Normalize names (remove quotes, lowercase, etc.)
    moe_name = normalize_name(moe_school.name)
    kg_name = normalize_name(kg_school.nameStr)

    # Use fuzzy matching
    ratio = fuzz.token_sort_ratio(moe_name, kg_name)

    return ratio > 85  # 85% similarity threshold
```

## Example Usage

### Fetch all kindergartens:
```bash
curl "https://kg.sofia.bg/api/public/kg/type/kinderGarden/all?filterType=by_region&kgType=0&regionId=0"
```

### Fetch kindergartens in Средец district:
```bash
curl "https://kg.sofia.bg/api/public/kg/type/kinderGarden/all?filterType=by_region&kgType=0&regionId=01"
```

### Using our adapter:
```python
import asyncio
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = KgSofiaBgAdapter(db=db)
        schools = await adapter.discover(limit=5)
        for school in schools:
            print(f'{school.name_i18n["bg"]} - {school.locations[0].address_i18n["bg"]}')

asyncio.run(test())
```

## Rate Limiting

The API doesn't appear to have strict rate limiting, but be respectful:
- Recommended: **10 requests/minute**
- The API returns all data in a single request (no pagination needed)
- Cache the results - the data doesn't change frequently

## Future Enhancements

1. **Geocoding integration** - Convert addresses to GPS coordinates
2. **Name matching** - Fuzzy match with MoE registry to get institutional IDs
3. **Contact parsing** - Extract email addresses from contacts array
4. **ESRI GIS integration** - Use esriId for advanced mapping features
