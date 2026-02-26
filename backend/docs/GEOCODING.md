# Geocoding Service Documentation

## Overview

The geocoding service converts Bulgarian addresses to GPS coordinates (lat/lng) for display on the map. It uses a provider pattern to support multiple geocoding APIs.

## Default Provider: OpenStreetMap Nominatim

By default, we use OSM Nominatim, which is:
- ✅ Free to use
- ✅ No API key required
- ✅ Good coverage for Bulgarian addresses
- ⚠️ Rate limited: 1 request per second
- ⚠️ Requires contact email in User-Agent

### Nominatim Usage Policy

We respect the [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/):
- **Rate limit**: Maximum 1 request per second (enforced in code)
- **User-Agent**: Must include application name and contact email
- **Caching**: Results are cached in database (no redundant requests)
- **Fair use**: Not for bulk geocoding of millions of addresses

## Configuration

Add to your `.env` file:

```bash
# Geocoding settings
GEOCODING_PROVIDER=nominatim
GEOCODING_CONTACT_EMAIL=your-email@example.com
```

## Usage

### CLI Tool

Geocode all locations without coordinates:

```bash
cd backend
uv run python scripts/geocode_locations.py
```

**Options:**
- `--limit N` - Geocode only first N locations (for testing)
- `--force` - Re-geocode all locations (even if they have coordinates)
- `--school-id ID` - Geocode only locations for specific school

**Examples:**

```bash
# Geocode first 10 locations (testing)
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

    # Geocode all missing locations
    summary = await service.geocode_all_missing()
    print(f"Success: {summary['success']}, Failed: {summary['failed']}")

    # Geocode specific school
    results = await service.geocode_school_locations(school_id=123)

    # Geocode single location
    location = await db.get(SchoolLocation, location_id)
    result = await service.geocode_location(location)
```

## Architecture

### Provider Pattern

The service uses an abstract provider interface so you can swap geocoding APIs:

```
BaseGeocodingProvider (abstract)
├── NominatimProvider (OpenStreetMap)
├── GoogleMapsProvider (not implemented yet)
└── MapboxProvider (not implemented yet)
```

### Database Caching

- Coordinates are stored in `school_locations.lat` and `school_locations.lng`
- If a location already has coordinates, geocoding is skipped (unless `force=True`)
- This means you only pay the API cost once per address

### Rate Limiting

The Nominatim provider enforces a 1-second delay between requests:

```python
# In NominatimProvider
MIN_REQUEST_INTERVAL = 1.0  # seconds

async def _enforce_rate_limit(self):
    # Wait if necessary to ensure 1 second between requests
    ...
```

## Switching Providers

To add a new provider (e.g., Google Maps):

1. **Create provider class** in `app/services/geocoding/`:

```python
from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult

class GoogleMapsProvider(BaseGeocodingProvider):
    @property
    def provider_name(self) -> str:
        return "google"

    async def geocode(self, address: str, country_code: str = "bg") -> GeocodingResult:
        # Implement Google Maps API call
        ...
```

2. **Update configuration** in `app/config.py`:

```python
# Add API key setting
google_maps_api_key: str = ""
```

3. **Update service initialization** in `app/services/geocoding/service.py`:

```python
if self.settings.geocoding_provider == "google":
    self.provider = GoogleMapsProvider(api_key=self.settings.google_maps_api_key)
elif self.settings.geocoding_provider == "nominatim":
    self.provider = NominatimProvider(...)
```

4. **Update .env**:

```bash
GEOCODING_PROVIDER=google
GOOGLE_MAPS_API_KEY=your-api-key
```

## Alternative Providers

### Google Maps Geocoding API
- ✅ Best accuracy
- ✅ Excellent Bulgarian coverage
- ❌ Requires API key
- ❌ Costs money (but has free tier)
- 📊 Pricing: $5 per 1000 requests (after free $200/month credit)

### Mapbox Geocoding API
- ✅ Good accuracy
- ✅ Modern API
- ❌ Requires API key
- ⚠️ Free tier: 100,000 requests/month
- 📊 Pricing: $0.75 per 1000 requests after free tier

### HERE Maps
- ✅ Good accuracy
- ✅ Free tier available
- ❌ Requires API key
- ⚠️ Free tier: 250,000 requests/month

## Workflow Integration

Geocoding fits into the scraping pipeline like this:

1. **Stage 1: Discovery** - Adapters fetch schools with addresses
2. **Stage 1.5: Geocoding** ← **YOU ARE HERE**
   - Convert addresses to coordinates
   - Store in database
3. **Stage 2: URL Validation** - Validate website URLs
4. **Stage 3: Navigation** - Crawl school websites
5. **Stage 4: Extraction** - Extract structured data
6. **Stage 5: Validation** - Validate extracted data
7. **Stage 6: Summarization** - Generate AI summaries

## Typical Usage Pattern

After running adapters:

```bash
# 1. Discover schools (creates schools with addresses but no coordinates)
uv run python scripts/run_adapter.py moe_registry

# 2. Geocode addresses (adds coordinates)
uv run python scripts/geocode_locations.py

# 3. Now schools are ready for map display
```

## Performance Notes

**Nominatim rate limit:**
- 1 request per second = 60 schools/minute = 3,600 schools/hour
- Sofia has ~1,000 schools total
- Full geocoding run: ~17 minutes

**Optimization:**
- Only geocode locations without coordinates (idempotent)
- Cache results in database
- Use `--limit` for testing to avoid wasting quota

## Error Handling

The service handles errors gracefully:

```python
result = await service.geocode_location(location)

if result.success:
    print(f"Success: ({result.lat}, {result.lng})")
else:
    print(f"Failed: {result.error}")
    # Location coordinates remain None in database
```

**Common errors:**
- **"No results found"** - Address not found by geocoder (bad address?)
- **"HTTP 429"** - Rate limit exceeded (wait longer between requests)
- **"HTTP 403"** - Invalid User-Agent or blocked by Nominatim

## Testing

Run tests:

```bash
cd backend
uv run pytest tests/test_geocoding_service.py -v
```

Tests use mocked API responses, so they don't make real requests to Nominatim.

## Monitoring

Check geocoding success rate:

```sql
-- Count locations with coordinates
SELECT COUNT(*) FROM school_locations WHERE lat IS NOT NULL;

-- Count locations without coordinates
SELECT COUNT(*) FROM school_locations WHERE lat IS NULL;

-- Schools with at least one location geocoded
SELECT COUNT(DISTINCT school_id)
FROM school_locations
WHERE lat IS NOT NULL;
```

## Troubleshooting

**Problem: All geocoding requests fail with "HTTP 403"**
- Solution: Check that `GEOCODING_CONTACT_EMAIL` is set in `.env`

**Problem: Some addresses return no results**
- Solution: Bulgarian addresses can be tricky. Check the address format.
- Try adding "София, България" to the address string.

**Problem: Rate limit errors (HTTP 429)**
- Solution: The code should already enforce 1 req/sec. If you still get 429, increase `MIN_REQUEST_INTERVAL` in `nominatim.py`.

**Problem: Coordinates are wrong**
- Solution: Check the address string. Bulgarian addresses need proper formatting.
- Example: "ул. Иван Вазов 15, София" works better than just "Иван Вазов 15"

## Best Practices

1. **Run geocoding after discovery** - Get all addresses first, then geocode them
2. **Use --limit for testing** - Don't waste quota on test runs
3. **Monitor failure rate** - If >10% fail, investigate address quality
4. **Don't re-geocode unnecessarily** - Coordinates are cached in DB
5. **Respect rate limits** - Nominatim can ban abusers
6. **Provide valid contact email** - Required by Nominatim policy

## Future Improvements

- [ ] Batch geocoding (when switching to paid provider)
- [ ] Automatic retry with exponential backoff for failed requests
- [ ] Address normalization before geocoding (improve success rate)
- [ ] Geocoding quality score (confidence level from provider)
- [ ] Manual coordinate correction UI (for failed/incorrect geocoding)
