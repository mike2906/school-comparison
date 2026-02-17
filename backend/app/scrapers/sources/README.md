# Source Adapters

This directory contains adapters for discovering schools from various data sources.

## Overview

Each country has its own subdirectory (`bg/`, `us/`, etc.) containing source adapters that implement the `BaseSourceAdapter` interface.

## Bulgaria (`bg/`)

### MoeRegistryAdapter (`moe_registry.py`)
**Source:** Ministry of Education API (https://ri-api.mon.bg)
**Coverage:** All schools and kindergartens in Bulgaria
**Data Quality:** ★★★★★ (Official government data)

**Provides:**
- Institutional ID (Код по НЕИСПУО) - critical for idempotency
- School names
- School type (state/private/international)
- Education level
- Region/municipality/town codes
- Physical addresses
- Phone numbers
- Emails
- Website URLs (when present in registry)

**Does NOT provide:**
- GPS coordinates
- Admission data or pricing

**Documentation:** See [backend/docs/MOE_API_DOCUMENTATION.md](../../docs/MOE_API_DOCUMENTATION.md)

---

### KgSofiaBgAdapter (`kg_sofia.py`)
**Source:** Sofia Municipality Kindergarten Portal (https://kg.sofia.bg)
**Coverage:** Sofia municipal kindergartens and schools with preparatory groups
**Data Quality:** ★★★★☆ (Official municipal data)

**Provides:**
- Physical addresses with districts
- Phone numbers
- Age groups served per location (derived from institution type)
- Shift information
- Registry metadata (`kg_sofia_*` attributes) used for enrichment and idempotency

**Does NOT provide:**
- Institutional IDs (in most cases, must be matched with MoE registry)
- Private kindergartens
- Full private-school coverage

**Status:** ✅ Implemented (API integration + enrichment merge behavior)

---

## Data Enrichment Strategy

### Phase 1: Discovery
Run both adapters to get complementary data:

1. **MoeRegistryAdapter** → Get comprehensive list with official IDs
2. **KgSofiaBgAdapter** → Enrich kindergartens with location/admission data

### Phase 2: Matching & Merging
Match records by:
- Exact institutional ID (when available)
- Fallback name + city + district matching
- Manual review for edge cases

### Phase 3: Website Discovery + Validation
For schools with missing/low-quality URLs:
- Stage 2 discovers and normalizes website candidates
- Stage 3 validates candidates (heuristics + optional LLM fallback)

### Phase 4: Website Navigation
For validated websites:
- Crawl same-domain pages
- Classify likely categories (admission/pricing/contact/etc.)
- Cache page content in `source_pages` for extraction stage

## Adding a New Source

1. Create a new adapter class inheriting from `BaseSourceAdapter`
2. Implement the `discover()` method
3. Add the `@register_adapter` decorator
4. Return `DiscoveredSchool` objects
5. Document the data source in this README

Example:
```python
from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.scrapers.sources import register_adapter

@register_adapter
class MySourceAdapter(BaseSourceAdapter):
    ADAPTER_NAME = "my_source"
    COUNTRY_CODE = "bg"
    CITY = "sofia"
    DESCRIPTION = "My data source"
    RATE_LIMIT = "10/m"

    async def discover(self, limit=None):
        # Fetch and parse data
        return [DiscoveredSchool(...)]
```

## Testing Adapters

```bash
# Test a specific adapter
cd backend
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
```

## Available Adapters

To see all registered adapters:
```python
from app.scrapers.sources import get_adapters_for_country

adapters = get_adapters_for_country("bg", "sofia")
for adapter_class in adapters:
    print(f"{adapter_class.ADAPTER_NAME}: {adapter_class.DESCRIPTION}")
```
