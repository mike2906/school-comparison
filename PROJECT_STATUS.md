# Sofia School Comparison - Project Status

**Last Updated:** March 19, 2026  
**Current Phase:** Scraping Pipeline - Stages 1-7 implemented, official BG NVO import live

---

## Current Pipeline Status

Implemented:
1. Stage 1 - Source discovery adapters (`MoeRegistryAdapter`, `KgSofiaBgAdapter`)
2. Stage 2 - Website discovery and URL normalization
3. Stage 3 - URL validation (heuristics + optional LLM fallback)
4. Stage 4 - Website navigation and page caching (`source_pages`)
5. Stage 5 - Extraction (pricing + general info, hash-based skip, quality gate)
6. Stage 6 - Data validation and monitoring spot checks
7. Stage 7 - Summarization
8. Independent NVO import stage (official `io.mon.bg` → `data.egov.bg` datasets)

Current local DB snapshot:
1. `summarized`: 464 schools
2. `extracted`: 5 schools
3. `extraction_failed`: 27 schools
4. `no_official_website`: 32 schools
5. `validated`: 1 school
6. `pending`: 11 schools
7. `exam_results`: 5115 official NVO rows across 247 Sofia schools
8. NVO year coverage: 2021-2025 for `nvo_4`, `nvo_7`, `nvo_10`

---

## Current Operational Commands

```bash
cd backend

# Stage 2
uv run python -m app.scrapers.cli run --stage discover-websites --city sofia --sync --limit 100

# Failed URL recovery
uv run python -m app.scrapers.cli run --stage recover-failed-urls --city sofia --sync --limit 50

# Stage 3
uv run python -m app.scrapers.cli run --stage validate-urls --city sofia --sync --limit 100

# Stage 4
uv run python -m app.scrapers.cli run --stage navigate --city sofia --sync --limit 100

# Stage 5
uv run python -m app.scrapers.cli run --stage extract --city sofia --sync --limit 100

# Stage 6
uv run python -m app.scrapers.cli run --stage validate-data --city sofia --sync --limit 100

# Stage 7
uv run python -m app.scrapers.cli run --stage summarize --city sofia --sync --limit 100

# Official NVO import (independent of website pipeline)
uv run python -m app.scrapers.cli run --stage nvo --city sofia --country bg --sync
```

---

## Config Notes

- `DATABASE_ECHO=true` enables SQL query logging (independent of `DEBUG`).
- SearXNG local setup requires root `.env` key: `SEARXNG_SECRET=...`
- New scraping tuning settings:
  - `WEBSITE_SEARCH_PROVIDER_DISABLE_SECONDS`
  - `URL_VALIDATION_MAX_CONCURRENCY`
  - `URL_RECOVERY_CONCURRENCY`

---

## References

- `README.md` - local setup and scraper CLI quick commands
- `AGENTS.md` - project conventions and architecture context
- `backend/app/scrapers/sources/README.md` - source adapter details
- `backend/docs/` - API and geocoding reference docs
