# Ministry of Education API Documentation

## Overview
The Bulgarian Ministry of Education provides a public API at `https://ri-api.mon.bg` for accessing the official registry of educational institutions.

## Base URL
```
https://ri-api.mon.bg
```

## Main Endpoints

### 1. Get Public Register (List Schools)
**Endpoint:** `POST /data/get/public-register`

**Request:**
```json
{
  "region": [22, 23],  // Array of region codes (22=Sofia city, 23=Sofia region)
  "isRIActive": 1      // 1 = only active institutions, 0 = all
}
```

**Optional filters:**
- `municipality`: Array of municipality codes
- `town`: Array of town/settlement codes
- `instType`: Array of institution types (1=school, 2=kindergarten)
- `financialSchoolType`: Array of ownership types (1=state, 2=municipal, 3=private)

**Response:**
```json
{
  "status": 1,
  "data": {
    "publicInstitutions": [
      {
        "id": 2200010,
        "instid": 2200010,        // Institutional ID (Код по НЕИСПУО) - PRIMARY KEY
        "name": "School Name",    // Bulgarian name
        "region": 22,             // Region code
        "municipality": 220,      // Municipality code
        "town": 68134,            // Town/settlement code
        "instType": 2,            // 1=school, 2=kindergarten
        "detailedSchoolType": 151, // See mapping below
        "financialSchoolType": 3, // 1=state, 2=municipal, 3=private
        "transformType": 5,       // Not relevant
        "instKind": 7,            // Not relevant
        "formName": "institution",
        "procID": 9632
      }
    ]
  }
}
```

### 2. Get Filter Options
**Get Institution Types:**
- `POST /data/get/instTypesNoLimit`
- Returns: `[{code: 1, label: "Училище"}, {code: 2, label: "Детска градина"}, ...]`

**Get Financial Types:**
- `POST /data/get/financialSchoolTypesNoLimit`
- Returns: `[{code: 1, label: "Държавно"}, {code: 2, label: "Общинско"}, ...]`

**Get Detailed School Types:**
- `POST /data/get/detailedSchoolTypes`
- Returns: `[{code: 121, label: "начално"}, ...]`

**Get Regions:**
- `POST /data/get/regionMultiple`
- Returns list of regions

**Get Municipalities:**
- `POST /data/get/municipalityMultiple`
- Request: `{"region": [22]}` // Filter by region

**Get Towns:**
- `POST /data/get/townMultiple`
- Request: `{"municipality": [220]}` // Filter by municipality

## Field Mappings

### instType (Institution Type)
| Code | Bulgarian | English | Our Schema |
|------|-----------|---------|------------|
| 1 | Училище | School | "school" |
| 2 | Детска градина | Kindergarten | "kindergarten" |
| 3 | Център за подкрепа за личностно развитие | Support center | (skip) |
| 4 | Център за специална образователна подкрепа | Special education center | (skip) |
| 5 | Специализирано обслужващо звено | Specialized service unit | (skip) |

### financialSchoolType (Ownership Type)
| Code | Bulgarian | English | Our Schema |
|------|-----------|---------|------------|
| 1 | Държавно | State | "state" |
| 2 | Общинско | Municipal | "state" |
| 3 | Частно | Private | "private" |
| 11 | Духовно | Religious | "state" |
| 12 | По силата на международен договор | International treaty | "international" |

### detailedSchoolType (Education Level)
| Code | Bulgarian | English | Our Schema |
|------|-----------|---------|------------|
| 121 | начално | Primary (grades 1-4) | "primary" |
| 122 | основно | Basic (grades 1-8) | "lower_secondary" |
| 123 | обединено | Combined (grades 1-12) | "upper_secondary" |
| 124 | средно | Secondary (grades 1-12) | "upper_secondary" |
| 125 | профилирана гимназия | Profiled gymnasium | "upper_secondary" |
| 126 | професионална гимназия | Vocational gymnasium | "upper_secondary" |
| 151 | детска градина | Kindergarten | "kindergarten" |
| 111-114 | духовно/изкуствата/културата/спортно | Specialized | "upper_secondary" |
| 131-134 | специално обучение | Special education | "lower_secondary" |
| 141 | към местата за лишаване от свобода | Prison schools | "lower_secondary" |
| 181 | по международен договор | International treaty | "upper_secondary" |

### Region Codes (София)
| Code | Bulgarian | English |
|------|-----------|---------|
| 22 | София-град | Sofia City |
| 23 | София област | Sofia Region |

## Data Available

### Public Register Endpoint (Basic Info)
**What the list endpoint provides:**
- Institutional ID (Код по НЕИСПУО)
- School name (Bulgarian only)
- School type and education level
- Region/Municipality/Town codes (not human-readable names)
- procID (needed for detail endpoint)

**What it does NOT provide:**
- Physical addresses
- GPS coordinates (lat/lng)
- Phone numbers
- Website URLs
- Admission information
- Pricing
- Exam results

### Detail Endpoint (Enhanced Data)
**See [MOE_API_ENHANCED_SUMMARY.md](MOE_API_ENHANCED_SUMMARY.md) for the two-phase approach.**

The `/data/get/institution` endpoint (requires `instid` + `procID`) provides:
- ✅ Full physical addresses
- ✅ Phone numbers
- ✅ Email addresses
- ✅ Website URLs
- ✅ Director names
- ✅ BULSTAT (company registration)
- ✅ Multiple locations (branches/departments)
- ❌ NO GPS coordinates (need geocoding)
- ❌ NO admission/pricing data (need website scraping)

## Notes for Implementation

1. **Use `instid` as the primary idempotency key** - this is the official institutional code
2. **Region/Municipality/Town codes need to be resolved** - make separate API calls to get human-readable names
3. **Location data must come from other sources** - school websites, kg.sofia.bg, geocoding, etc.
4. **Rate limiting** - Be respectful, ~10 requests/minute is reasonable
5. **Active institutions only** - Always set `isRIActive: 1` to exclude closed schools

## Testing the API

### Using curl:
```bash
curl -X POST https://ri-api.mon.bg/data/get/public-register \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"region": [22, 23], "isRIActive": 1}'
```

### Using our adapter:
```bash
cd backend
uv run python -c "
import asyncio
from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = MoeRegistryAdapter(db=db)
        schools = await adapter.discover(limit=5)
        for school in schools:
            print(f'{school.institutional_id}: {school.name_i18n[\"bg\"]} ({school.education_level})')

asyncio.run(test())
"
```

## Integration with Scraping Pipeline

The MoeRegistryAdapter is now part of Stage 1 (Discovery) of the scraping pipeline:

1. **Stage 1: Discovery** - MoeRegistryAdapter fetches all schools from API
2. **Stage 2: URL Validation** - Validate website URLs (if found elsewhere)
3. **Stage 3: Navigation** - Crawl school websites to find contact info, addresses
4. **Stage 4: Extraction** - Extract structured data (prices, languages, etc.)
5. **Stage 5: Validation** - Validate extracted data
6. **Stage 6: Summarization** - Generate AI summaries

## Future Enhancements

1. **Fetch region/municipality/town names** - Resolve codes to human-readable names
2. **Cache filter metadata** - Store institution types, financial types for faster lookups
3. **Support other regions** - Extend beyond Sofia (Plovdiv, Varna, etc.)
4. **Enrichment pipeline** - Match MoE data with kg.sofia.bg data by name/address
