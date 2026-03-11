# Sofia School Comparison - Project Status

**Last Updated:** March 11, 2026  
**Current Phase:** Scraping Pipeline - Stages 1-6 implemented, Stage 7 pending

---

## Current Pipeline Status

Implemented:
1. Stage 1 - Source discovery adapters (`MoeRegistryAdapter`, `KgSofiaBgAdapter`)
2. Stage 2 - Website discovery and URL normalization
3. Stage 3 - URL validation (heuristics + optional LLM fallback)
4. Stage 4 - Website navigation and page caching (`source_pages`)
5. Stage 5 - Extraction (pricing + general info, hash-based skip, quality gate)
6. Stage 6 - Data validation and monitoring spot checks

Not implemented yet:
1. Stage 7 - Summarization

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
