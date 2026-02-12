# Geocoding Implementation Summary

## What Was Built

A flexible geocoding service that converts Bulgarian addresses to GPS coordinates (lat/lng) using OpenStreetMap Nominatim, with the ability to swap in other providers (Google Maps, Mapbox, HERE) later.

## Key Features

### ✅ Provider Pattern (Swappable)
- Abstract `BaseGeocodingProvider` interface
- Concrete `NominatimProvider` implementation (OSM)
- Easy to add new providers without changing calling code

### ✅ Respects Nominatim Usage Policy
- **Rate limiting**: Enforces 1 request per second (OSM requirement)
- **User-Agent**: Includes contact email (configured via `.env`)
- **Caching**: Checks database before making API calls
- **Fair use**: Only geocodes locations without coordinates

### ✅ Database Integration
- Stores results in existing `school_locations.lat` and `school_locations.lng`
- Idempotent: skips locations that already have coordinates
- Graceful error handling: failed geocoding doesn't block pipeline

### ✅ CLI Tool
- `scripts/geocode_locations.py` - command-line interface
- Options: `--limit`, `--force`, `--school-id`
- Progress reporting and summary statistics

### ✅ Comprehensive Tests
- 8 new tests covering all scenarios
- Mocked API responses (no real API calls in tests)
- Tests for success, failure, rate limiting, caching
- **All 166 tests pass**

## Files Created

```
backend/
├── app/
│   ├── config.py                                    # Added geocoding settings
│   └── services/
│       └── geocoding/
│           ├── __init__.py                          # Package exports
│           ├── base.py                              # Abstract provider interface
│           ├── nominatim.py                         # OSM Nominatim implementation
│           └── service.py                           # Main geocoding service
├── scripts/
│   └── geocode_locations.py                         # CLI tool
├── tests/
│   └── test_geocoding_service.py                    # 8 comprehensive tests
├── docs/
│   ├── GEOCODING.md                                 # Full documentation
│   └── GEOCODING_IMPLEMENTATION_SUMMARY.md          # This file
└── .env.example                                     # Added geocoding settings
```

## Configuration

Add to `.env`:

```bash
# Geocoding settings
GEOCODING_PROVIDER=nominatim
GEOCODING_CONTACT_EMAIL=your-email@example.com
```

## Usage

### Quick Start

```bash
# 1. Run adapter to discover schools (creates addresses)
uv run python scripts/run_adapter.py moe_registry

# 2. Geocode addresses (adds coordinates)
uv run python scripts/geocode_locations.py

# 3. Schools now have coordinates for map display
```

### CLI Examples

```bash
# Geocode all missing locations
uv run python scripts/geocode_locations.py

# Test with limit (only first 10)
uv run python scripts/geocode_locations.py --limit 10

# Geocode specific school
uv run python scripts/geocode_locations.py --school-id 123

# Force re-geocode all (careful!)
uv run python scripts/geocode_locations.py --force
```

### Programmatic Usage

```python
from app.database import async_session_maker
from app.services.geocoding.service import GeocodingService

async with async_session_maker() as db:
    service = GeocodingService(db=db)

    # Geocode all missing
    summary = await service.geocode_all_missing()
    # Returns: {"total": 100, "success": 95, "failed": 5}

    # Geocode specific school
    results = await service.geocode_school_locations(school_id=123)

    # Geocode single location
    location = await db.get(SchoolLocation, location_id)
    result = await service.geocode_location(location)
```

## Performance

**Nominatim Rate Limit:**
- 1 request per second = 60 schools/minute
- Sofia has ~1,000 schools total
- **Full geocoding run: ~17 minutes**

**Idempotency:**
- Only geocodes locations without coordinates
- Re-running is fast (skips already-geocoded locations)
- Use `--limit` for testing to avoid wasting quota

## Error Handling

The service handles errors gracefully:

```python
result = await service.geocode_location(location)

if result.success:
    print(f"✓ Success: ({result.lat}, {result.lng})")
else:
    print(f"✗ Failed: {result.error}")
    # Location coordinates remain None in database
    # Can retry later or investigate address quality
```

**Common errors:**
- **"No results found"** - Bad/incomplete address
- **"HTTP 429"** - Rate limit exceeded (shouldn't happen with our 1s delay)
- **"HTTP 403"** - Missing/invalid User-Agent (check `GEOCODING_CONTACT_EMAIL`)

## Architecture Decisions

### Why Provider Pattern?
- **Flexibility**: Easy to switch providers (OSM → Google → Mapbox)
- **Testing**: Can inject mock providers in tests
- **No vendor lock-in**: Not tied to one geocoding API

### Why Database Caching?
- **Cost**: Free tier limits or per-request pricing
- **Speed**: Instant lookup vs API call
- **Reliability**: Works even if API is down (after initial geocoding)
- **Idempotency**: Safe to re-run scripts without waste

### Why Nominatim by Default?
- **Free**: No API key, no billing
- **Good coverage**: Works well for Bulgarian addresses
- **No lock-in**: Can upgrade to paid provider later if needed
- **Respects policies**: We follow OSM usage guidelines

## Switching Providers Later

When you need better accuracy or higher rate limits:

1. **Implement new provider** (e.g., `GoogleMapsProvider` in `app/services/geocoding/`)
2. **Add API key to config** (`google_maps_api_key: str = ""`)
3. **Update service init** to use new provider based on `geocoding_provider` setting
4. **Update .env** (`GEOCODING_PROVIDER=google`)

**No changes needed** in adapters, scripts, or other code that uses the service.

## Testing

```bash
# Run geocoding tests only
uv run pytest tests/test_geocoding_service.py -v

# Run all tests (verify nothing broke)
uv run pytest

# Result: 166 tests pass ✓
```

**Test coverage:**
- ✅ Nominatim provider basics (success, failure, rate limiting)
- ✅ Service geocoding (single location, all locations, school-specific)
- ✅ Database caching (skip already-geocoded locations)
- ✅ Error handling (bad addresses, API errors)
- ✅ All tests use mocked responses (no real API calls)

## Integration with Pipeline

**Current Position:**

```
Stage 1: Discovery (MoeRegistryAdapter, KgSofiaBgAdapter)
   ↓
   → Schools created with addresses
   ↓
Stage 1.5: Geocoding ← YOU ARE HERE
   ↓
   → Addresses converted to coordinates
   ↓
Stage 2: URL Validation
   ↓
Stage 3: Navigation
   ↓
Stage 4: Extraction
   ↓
Stage 5: Validation
   ↓
Stage 6: Summarization
```

**When to run:**
- After discovery adapters (MoeRegistryAdapter, KgSofiaBgAdapter)
- Before displaying schools on map (frontend needs lat/lng)

## Monitoring Success Rate

Check geocoding status:

```sql
-- Locations with coordinates
SELECT COUNT(*) FROM school_locations WHERE lat IS NOT NULL;

-- Locations without coordinates
SELECT COUNT(*) FROM school_locations WHERE lat IS NULL;

-- Success rate
SELECT
    COUNT(CASE WHEN lat IS NOT NULL THEN 1 END)::float /
    COUNT(*)::float * 100 AS success_rate_pct
FROM school_locations;
```

## Known Limitations

1. **Rate limit**: 1 req/sec means bulk geocoding takes time (~17 min for all Sofia schools)
2. **Address quality**: Some addresses may not be found by Nominatim
3. **No manual corrections**: No UI yet to manually fix incorrect coordinates
4. **Single country**: Currently hardcoded for Bulgaria (`country_code="bg"`)

## Future Enhancements

- [ ] Address normalization before geocoding (improve success rate)
- [ ] Batch geocoding when using paid providers
- [ ] Automatic retry with exponential backoff
- [ ] Geocoding confidence score
- [ ] Manual coordinate correction UI
- [ ] Multi-country support (pass country_code from school.country_code)

## Alternative Providers

**Google Maps Geocoding API:**
- ✅ Best accuracy
- ❌ Requires API key + billing
- 💰 $5 per 1000 requests (after $200/month free credit)

**Mapbox Geocoding API:**
- ✅ Good accuracy, modern API
- ❌ Requires API key
- 💰 100,000 requests/month free, then $0.75 per 1000

**HERE Maps:**
- ✅ Good accuracy
- ❌ Requires API key
- 💰 250,000 requests/month free

## Summary

✅ **Complete** - Geocoding service is production-ready
✅ **Tested** - All 166 tests pass (8 new tests added)
✅ **Documented** - Full docs in `GEOCODING.md`
✅ **Flexible** - Easy to swap providers later
✅ **Respectful** - Follows Nominatim usage policy
✅ **Idempotent** - Safe to re-run, only geocodes missing coordinates
✅ **CLI Ready** - `scripts/geocode_locations.py` for one-off runs

**Next step in pipeline**: Run the geocoding script after discovery to populate coordinates, then proceed to URL validation (Stage 2).
