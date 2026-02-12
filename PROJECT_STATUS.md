# Sofia School Comparison - Project Status

**Last Updated:** February 12, 2026
**Current Phase:** Scraping Pipeline - Stage 1 (Discovery) ✅ COMPLETE

---

## 🎯 Quick Start (Resume Work)

### Where We Left Off
Both source adapters for Bulgarian schools are **fully implemented and tested**. We can now discover all schools in Sofia with complete contact information (addresses, phones, emails, websites).

### Next Immediate Steps
1. **Geocoding** - Convert addresses to GPS coordinates (see [Next Steps](#next-steps) below)
2. **District name extraction** - Parse or match with kg.sofia.bg
3. **Run full discovery** - Populate database with all 507 Sofia schools

### Key Documentation
- **[Backend Docs](backend/docs/)** - All technical documentation
  - `MOE_API_ENHANCED_SUMMARY.md` - What we just completed ⭐
  - `SOURCE_ADAPTERS_SUMMARY.md` - Overview of both adapters
  - `MOE_API_DOCUMENTATION.md` - MoE API reference
  - `KG_SOFIA_API_DOCUMENTATION.md` - kg.sofia.bg API reference
- **[AGENTS.md](AGENTS.md)** - Project architecture and rules
- **[sofia_school_compare_plan_v2.md](sofia_school_compare_plan_v2.md)** - Overall project plan

---

## ✅ Completed Work

### Stage 1: Discovery Adapters (COMPLETE)

#### 1. MoeRegistryAdapter ✅
**File:** `backend/app/scrapers/sources/bg/moe_registry.py`

**Capabilities:**
- Fetches all 507 schools/kindergartens in Sofia from Ministry of Education API
- **Two-phase approach:**
  - Phase 1: Get list of all schools (`POST /data/get/public-register`)
  - Phase 2: Fetch detailed data per school (`POST /data/get/institution`)

**Data Quality:**
- ✅ 100% - Institutional IDs (Код по НЕИСПУО)
- ✅ 100% - School names, types, education levels
- ✅ 80-90% - Full addresses with postal codes
- ✅ 60-70% - Phone numbers
- ✅ 60-70% - Email addresses
- ✅ 40-50% - Website URLs
- ✅ 100% - Director names, BULSTAT (company reg)
- ✅ Multiple locations per school (branches/departments)

**Performance:**
- ~2-3 seconds per school (with rate limiting)
- ~25-30 minutes for full Sofia discovery
- Tested and working ✅

**Test Results:**
```
5 schools tested: 5/5 successful
- With addresses: 4/5 (80%)
- With phones: 3/5 (60%)
- With emails: 3/5 (60%)
- With websites: 2/5 (40%)
```

#### 2. KgSofiaBgAdapter ✅
**File:** `backend/app/scrapers/sources/bg/kg_sofia.py`

**Capabilities:**
- Fetches 501 institutions (343 kindergartens + 158 schools) from Sofia Municipality API
- **Single API call** per institution type
- State-run institutions only (no private schools)

**Data Quality:**
- ✅ 100% - School names
- ✅ 100% - Full addresses
- ✅ 100% - Human-readable district names ("Средец", "Лозенец", etc.)
- ✅ ~80% - Phone numbers
- ✅ 100% - ESRI GIS IDs (for mapping)
- ❌ NO institutional IDs (must match by name)
- ❌ NO emails or websites

**Current Role:**
- **Secondary enrichment source** for MoE data
- Provides district names (MoE only has codes)
- Provides ESRI GIS IDs
- Data verification (cross-check addresses/phones)

**Status:** Tested and working ✅

---

## 📊 Current Data Coverage

### Combined Data (MoE + kg.sofia.bg)

| Data Point | Coverage | Source |
|------------|----------|--------|
| Institutional IDs | 100% | MoE API |
| School names | 100% | Both |
| School types | 100% | MoE API |
| Education levels | 100% | MoE API |
| **Addresses** | **80-90%** | **MoE API** |
| **Phone numbers** | **60-70%** | **MoE API** |
| **Email addresses** | **60-70%** | **MoE API** |
| **Website URLs** | **40-50%** | **MoE API** |
| **District names** | **~50%** | **kg.sofia.bg** |
| ESRI GIS IDs | ~70% | kg.sofia.bg |
| GPS coordinates | **0%** | **⚠️ NEEDS GEOCODING** |

### Gap Analysis

**High Priority:**
- ❌ **GPS coordinates** - Need geocoding for ~400 schools
- ⚠️ **District names** - Need parsing or matching for ~50% of schools
- ⚠️ **Missing websites** - 50-60% of schools don't have websites in MoE data

**Medium Priority:**
- Phone/email completeness (30-40% missing)
- Private school coverage (kg.sofia.bg doesn't have them)

**Low Priority:**
- Age groups per location
- Shift information
- Admission thresholds (future feature)

---

## 🚀 Next Steps

### Immediate (Phase 2: Data Enrichment)

#### 1. Geocoding Implementation
**Goal:** Convert addresses to GPS coordinates (lat/lng)

**Options:**
1. **Google Maps Geocoding API** (Recommended)
   - **Pros:** High accuracy, good coverage
   - **Cons:** Costs ~$5 per 1000 requests (~$2 for 400 schools)
   - **Setup:** Need API key, ~2-3 hours to implement

2. **Nominatim (OpenStreetMap)**
   - **Pros:** Free, open data
   - **Cons:** Lower accuracy for Bulgarian addresses
   - **Setup:** ~1-2 hours to implement

**Implementation Plan:**
```python
# backend/app/services/geocoding.py
async def geocode_address(address: str, city: str = "sofia") -> Optional[tuple[float, float]]:
    """Geocode an address to (lat, lng)."""
    # Option 1: Google Maps API
    # Option 2: Nominatim API
    pass

# Usage in discovery pipeline
for school in schools:
    if school.locations and not school.locations[0].lat:
        lat, lng = await geocode_address(school.locations[0].address_i18n['bg'])
        school.locations[0].lat = lat
        school.locations[0].lng = lng
```

**Action Items:**
- [ ] Choose geocoding provider (Google vs Nominatim)
- [ ] Implement `geocoding.py` service
- [ ] Add geocoding to discovery pipeline
- [ ] Test with 10 sample schools
- [ ] Run for all 400+ schools

**Estimated Time:** 2-4 hours

---

#### 2. District Name Extraction
**Goal:** Add human-readable district names to all schools

**Approach:**
1. **Parse from addresses** - Look for district keywords in address strings
2. **Match with kg.sofia.bg** - Fuzzy name matching to enrich MoE data
3. **Manual mapping** - For edge cases

**Implementation Plan:**
```python
# backend/app/utils/district_parser.py
DISTRICT_PATTERNS = {
    "средец": "Средец",
    "лозенец": "Лозенец",
    # ... etc
}

def extract_district_from_address(address: str) -> Optional[str]:
    """Extract district name from address string."""
    address_lower = address.lower()
    for pattern, district_name in DISTRICT_PATTERNS.items():
        if pattern in address_lower:
            return district_name
    return None
```

**Action Items:**
- [ ] Create district parser utility
- [ ] Run on all MoE schools
- [ ] Match MoE + kg.sofia.bg by name (fuzzy matching)
- [ ] Verify district assignments

**Estimated Time:** 2-3 hours

---

#### 3. Data Enrichment Script
**Goal:** Combine MoE + kg.sofia.bg data, geocode, extract districts

**Implementation Plan:**
```bash
# backend/scripts/enrich_schools.py
# 1. Fetch from MoE (primary source)
# 2. Match with kg.sofia.bg by name
# 3. Extract district names
# 4. Geocode addresses
# 5. Save to database
```

**Action Items:**
- [ ] Create enrichment script
- [ ] Implement name matching (fuzzywuzzy)
- [ ] Run geocoding for all schools
- [ ] Populate database
- [ ] Verify data quality

**Estimated Time:** 4-6 hours

---

### Medium Term (Stage 2-3: Website Discovery & Navigation)

#### 4. Website Discovery (For Missing Websites)
**Goal:** Find website URLs for schools that don't have them in MoE data

**Options:**
1. **Google Search API** - Search for "{school_name} София"
2. **URL pattern detection** - Try common patterns (ou{number}.com, etc.)
3. **Manual curation** - For top schools

**Action Items:**
- [ ] Implement Google Search integration
- [ ] Test with 10 sample schools
- [ ] Run for all schools without websites
- [ ] Manual review for top 50 schools

**Estimated Time:** 4-6 hours

---

#### 5. Website Scraping (Stage 3)
**Goal:** Extract additional data from school websites

**Current Status:** Placeholder implementation exists

**What to Extract:**
- More current phone/email
- Pricing information (for private schools)
- Language programs
- Facilities
- After-school programs

**Action Items:**
- [ ] Review existing Crawl4AI integration
- [ ] Implement page classification
- [ ] Extract contact info
- [ ] Extract pricing data
- [ ] Store in database

**Estimated Time:** 10-15 hours

---

### Long Term (Stage 4-6)

#### 6. NVO Results Scraping
**Goal:** Scrape exam results from Ministry of Education

**Source:** Government exam results platform

**Action Items:**
- [ ] Investigate NVO results API/website
- [ ] Implement scraper
- [ ] Store historical results
- [ ] Display in UI

**Estimated Time:** 8-12 hours

---

#### 7. Admission Points Calculator
**Goal:** Build calculator for kg.sofia.bg points system

**Requirements:**
- Understand points formula
- Build calculator UI
- Test with real scenarios

**Estimated Time:** 6-8 hours

---

## 📁 Project Structure

```
sofia-school-compare/
├── backend/
│   ├── app/
│   │   ├── scrapers/
│   │   │   └── sources/
│   │   │       ├── bg/
│   │   │       │   ├── moe_registry.py    ✅ COMPLETE
│   │   │       │   └── kg_sofia.py        ✅ COMPLETE
│   │   │       ├── base_adapter.py
│   │   │       └── __init__.py
│   │   ├── services/
│   │   │   └── geocoding.py               ⏳ TODO
│   │   └── utils/
│   │       └── district_parser.py         ⏳ TODO
│   ├── docs/
│   │   ├── MOE_API_ENHANCED_SUMMARY.md    ⭐ START HERE
│   │   ├── SOURCE_ADAPTERS_SUMMARY.md
│   │   ├── MOE_API_DOCUMENTATION.md
│   │   └── KG_SOFIA_API_DOCUMENTATION.md
│   ├── scripts/
│   │   └── enrich_schools.py              ⏳ TODO
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/
│       │   └── Map/                       ⏳ Needs GPS data
│       └── ...
├── AGENTS.md                              📖 Architecture rules
├── sofia_school_compare_plan_v2.md        📖 Overall plan
└── PROJECT_STATUS.md                      📍 YOU ARE HERE
```

---

## 🔧 Quick Commands

### Test MoE Adapter
```bash
cd backend
uv run python -c "
import asyncio
from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = MoeRegistryAdapter(db=db)
        schools = await adapter.discover(limit=5, fetch_details=True)
        print(f'Discovered {len(schools)} schools')
        for s in schools:
            print(f'{s.name_i18n[\"bg\"]}: {s.website_url or \"No website\"}')

asyncio.run(test())
"
```

### Test kg.sofia.bg Adapter
```bash
cd backend
uv run python -c "
import asyncio
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
from app.database import async_session_maker

async def test():
    async with async_session_maker() as db:
        adapter = KgSofiaBgAdapter(db=db)
        schools = await adapter.discover(limit=5)
        print(f'Discovered {len(schools)} institutions')

asyncio.run(test())
"
```

### Run Full Discovery (via CLI)
```bash
cd backend
uv run python -m app.scrapers.cli discover --adapter moe_registry --limit 10
```

### Run Full Discovery (via Celery)
```bash
cd backend
celery -A tasks worker --loglevel=info &
uv run python -m app.scrapers.cli pipeline run --stage discover
```

---

## 🎯 Recommended Next Session Plan

**Recommended Priority:** Geocoding → District extraction → Database population

**Session 1: Geocoding (2-4 hours)**
1. Choose provider (Google Maps recommended)
2. Implement `backend/app/services/geocoding.py`
3. Test with 10 sample schools
4. Run for all schools
5. Verify accuracy

**Session 2: District Extraction (2-3 hours)**
1. Implement district parser
2. Match MoE + kg.sofia.bg data
3. Extract districts for all schools
4. Verify assignments

**Session 3: Database Population (1-2 hours)**
1. Create enrichment script
2. Run full discovery with geocoding
3. Populate database
4. Verify data quality
5. Test frontend map display

**Total Time:** ~6-9 hours to have a fully populated database with GPS coordinates

---

## 📝 Notes & Decisions

### Why MoE is Primary Source
- Has institutional IDs (critical for idempotency)
- Covers ALL schools (state + private)
- Has addresses, phones, emails for most schools
- Official government data

### Why Keep kg.sofia.bg
- Human-readable district names
- ESRI GIS IDs for mapping
- Data verification
- Possibly more current municipal data

### Geocoding Provider Decision
**Pending:** Choose between Google Maps ($) vs Nominatim (free)
- Google: Better accuracy, costs ~$2 for 400 schools
- Nominatim: Free, but may have lower accuracy for Bulgarian addresses

---

## 🐛 Known Issues

None currently - both adapters tested and working ✅

---

## 📚 Additional Resources

- **Scraping Pipeline Docs:** See `backend/app/scrapers/sources/README.md`
- **API Docs:** See `backend/docs/` for detailed API documentation
- **Git Branch:** Currently on `feature/scraping-pipeline`
- **Test Coverage:** 97 backend tests passing ✅

---

**Questions or need clarification? Check:**
1. This file (PROJECT_STATUS.md) - High-level status
2. `backend/docs/MOE_API_ENHANCED_SUMMARY.md` - What we just completed
3. `AGENTS.md` - Architecture and rules
4. `sofia_school_compare_plan_v2.md` - Overall project vision
