# Source Adapters Implementation Summary

## Overview

Both source adapters for Bulgarian schools are now fully implemented with real API integrations!

## Completed Adapters

### 1. MoeRegistryAdapter ✅
**Source:** Ministry of Education API (https://ri-api.mon.bg)
**File:** `backend/app/scrapers/sources/bg/moe_registry.py`
**Documentation:** `backend/docs/MOE_API_DOCUMENTATION.md`

**Coverage:**
- 507 institutions in Sofia (city + region)
- All schools and kindergartens (state, private, international)
- Complete national registry

**Data Quality:** ⭐⭐⭐⭐⭐ (Official government data)

**Provides:**
- ✅ Institutional ID (Код по НЕИСПУО) - **critical for idempotency**
- ✅ School names (Bulgarian)
- ✅ School type (state/private/international)
- ✅ Education level (kindergarten/primary/lower_secondary/upper_secondary)
- ✅ Region/municipality/town codes

**Missing:**
- ❌ Physical addresses
- ❌ GPS coordinates
- ❌ Phone numbers
- ❌ Website URLs

**Test Results:**
```bash
Total institutions found: 507
Successfully parsed: 507
API response time: ~1-2 seconds
```

---

### 2. KgSofiaBgAdapter ✅
**Source:** Sofia Municipality API (https://kg.sofia.bg/api/public)
**File:** `backend/app/scrapers/sources/bg/kg_sofia.py`
**Documentation:** `backend/docs/KG_SOFIA_API_DOCUMENTATION.md`

**Coverage:**
- 343 kindergartens (all Sofia districts)
- 158 schools with preparatory groups
- Total: 501 institutions

**Data Quality:** ⭐⭐⭐⭐☆ (Official municipal data, state-run only)

**Provides:**
- ✅ School names (Bulgarian)
- ✅ Full address strings
- ✅ District/region names (human-readable!)
- ✅ Phone numbers
- ✅ ESRI GIS IDs

**Missing:**
- ❌ Institutional IDs (must match with MoE data)
- ❌ GPS coordinates (need geocoding)
- ❌ Private schools/kindergartens
- ❌ Age groups, admission thresholds

**Test Results:**
```bash
Total institutions found: 501 (343 KG + 158 schools)
Successfully parsed: 501
API response time: ~2-3 seconds total
```

---

## Data Integration Strategy

### Phase 1: Parallel Discovery (Current)
Run both adapters independently:

```bash
# Terminal 1: MoE Registry
uv run python -m app.scrapers.cli discover --adapter moe_registry

# Terminal 2: kg.sofia.bg
uv run python -m app.scrapers.cli discover --adapter kg_sofia_bg
```

### Phase 2: Name Matching & Enrichment (Next)
1. **MoE data as source of truth** (has institutional IDs)
2. **Match kg.sofia.bg data by name** using fuzzy matching
3. **Enrich MoE schools** with addresses, phone numbers, districts
4. **Store in database** with both institutional ID and location data

**Matching Algorithm:**
```python
from fuzzywuzzy import fuzz

def match_schools(moe_name, kg_name):
    # Normalize names
    moe_clean = normalize_name(moe_name)
    kg_clean = normalize_name(kg_name)

    # Fuzzy match
    ratio = fuzz.token_sort_ratio(moe_clean, kg_clean)

    return ratio > 85  # 85% similarity threshold
```

### Phase 3: Geocoding (Future)
Convert addresses to GPS coordinates:
- Use Google Maps Geocoding API, or
- Use Nominatim (OpenStreetMap), or
- Manual entry for mismatches

---

## Running the Adapters

### Quick Test (5 schools each):
```bash
cd backend

# Test MoE adapter
uv run python -c "
import asyncio
from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = MoeRegistryAdapter(db=db)
        schools = await adapter.discover(limit=5)
        for s in schools:
            print(f'{s.institutional_id}: {s.name_i18n[\"bg\"]}')

asyncio.run(test())
"

# Test kg.sofia.bg adapter
uv run python -c "
import asyncio
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = KgSofiaBgAdapter(db=db)
        schools = await adapter.discover(limit=5)
        for s in schools:
            loc = s.locations[0] if s.locations else None
            addr = loc.address_i18n.get('bg', '') if loc else 'N/A'
            print(f'{s.name_i18n[\"bg\"]} - {addr}')

asyncio.run(test())
"
```

### Full Discovery (via Celery pipeline):
```bash
# Start Celery worker
celery -A tasks worker --loglevel=info &

# Run discovery stage
uv run python -m app.scrapers.cli pipeline run --stage discover
```

---

## What's Next?

### Immediate Next Steps:
1. ✅ ~~Implement MoeRegistryAdapter~~ (DONE)
2. ✅ ~~Implement KgSofiaBgAdapter~~ (DONE)
3. ⏳ **Create name matching/enrichment script** (Phase 2)
4. ⏳ **Add geocoding for addresses** (Phase 3)
5. ⏳ **Implement website scraping** (Stage 3 of pipeline)

### Later Phases:
- Stage 3: Navigate school websites
- Stage 4: Extract structured data (prices, languages, etc.)
- Stage 5: Validate extracted data
- Stage 6: Generate AI summaries

---

## Data Completeness

| Field | MoE API | kg.sofia.bg | Combined |
|-------|---------|-------------|----------|
| Institutional ID | ✅ | ❌ | ✅ |
| Name | ✅ | ✅ | ✅ |
| School Type | ✅ | ⚠️ (state only) | ✅ |
| Education Level | ✅ | ✅ | ✅ |
| Address | ❌ | ✅ | ✅ |
| District | ❌ | ✅ | ✅ |
| GPS Coordinates | ❌ | ❌ | ⏳ (geocoding) |
| Phone | ❌ | ✅ | ✅ |
| Website URL | ❌ | ❌ | ⏳ (Stage 3) |
| Pricing | ❌ | ❌ | ⏳ (Stage 4) |
| Admission Data | ❌ | ❌ | ⏳ (Stage 4) |

---

## Files Changed

### New Files:
- `backend/docs/MOE_API_DOCUMENTATION.md` - MoE API reference
- `backend/docs/KG_SOFIA_API_DOCUMENTATION.md` - kg.sofia.bg API reference
- `backend/app/scrapers/sources/README.md` - Source adapters overview
- `backend/docs/SOURCE_ADAPTERS_SUMMARY.md` - This file

### Modified Files:
- `backend/app/scrapers/sources/bg/moe_registry.py` - Fully implemented
- `backend/app/scrapers/sources/bg/kg_sofia.py` - Fully implemented

---

## Success Metrics

✅ Both adapters successfully connect to real APIs
✅ Both adapters parse data correctly
✅ Combined coverage: ~1000 institutions (507 MoE + 501 kg.sofia.bg with some overlap)
✅ All code tested and working
✅ Comprehensive documentation created

**Ready for Phase 2: Data Enrichment & Matching!**
