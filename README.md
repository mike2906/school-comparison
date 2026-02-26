# School Comparison

Bilingual (Bulgarian/English) web app for parents in Sofia to discover, filter, and compare kindergartens and schools.

## Features
- Interactive map of school locations
- Filter by enrollment year (age group)
- Compare private schools side-by-side (pricing, facilities)
- View NVO exam results for state schools
- Admission thresholds and points history
- Bilingual UI (Bulgarian primary, English secondary)

## Tech Stack
- Backend: FastAPI, SQLAlchemy 2.0 (async), PostgreSQL
- Frontend: React 18 + Vite, Leaflet, Tailwind, i18next

## Scraping Pipeline Status
Implemented:
- Stage 1: School discovery adapters
- Stage 2: Website discovery + normalization
- Stage 3: URL validation (heuristics + optional LLM fallback)
- Stage 4: Website navigation + page caching
- Stage 5: Extraction (pricing + general info, hash-based skip, quality gate)

Not implemented yet:
- Stage 6+: data validation, summarization

## Local Development
```bash
# Optional but recommended (gitignored root .env):
# SEARXNG_SECRET=your-long-random-secret

# Databases
docker-compose up -d

# Backend
cd backend
uv run uvicorn app.main:app --reload

# Frontend
cd frontend
npm run dev
```

If `searxng` crash-loops with `server.secret_key is not changed`, set `SEARXNG_SECRET` in a root `.env` file (project root, not `backend/.env`) and restart:

```bash
docker compose up -d --force-recreate searxng
```

Backend env note: `DEBUG=true` no longer enables SQL logging by itself. Use `DATABASE_ECHO=true` when you explicitly want SQL query logs.

## Scraper CLI Quick Commands
```bash
cd backend

# Batch website discovery (Stage 2)
uv run python -m app.scrapers.cli run --stage discover-websites --city sofia --sync --limit 100

# Recover failed URL candidates
uv run python -m app.scrapers.cli run --stage recover-failed-urls --city sofia --sync --limit 50

# Validate URLs (Stage 3)
uv run python -m app.scrapers.cli run --stage validate-urls --city sofia --sync --limit 100

# Navigate validated websites (Stage 4)
uv run python -m app.scrapers.cli run --stage navigate --city sofia --sync --limit 100

# Extract structured data from navigated pages (Stage 5)
uv run python -m app.scrapers.cli run --stage extract --city sofia --sync --limit 100
```

## Recommended Single-School Workflow
For problematic domains (like bot-protected sites), use the standard per-school flow:
1. `validate-urls`
2. `navigate` (now includes challenge-aware retry and undetected+stealth second fallback)
3. `extract`

```bash
cd backend
scripts/run_school_workflow.sh --school-id 182
```

## Extraction Tuning
Set these in `backend/.env` when tuning quality/cost:

```bash
EXTRACTION_PRIMARY_TIER=medium         # cheap|medium
EXTRACTION_QUALITY_GATE_ENABLED=true
EXTRACTION_GENERAL_INFO_MIN_QUALITY_SCORE=4
NAV_CONTENT_EXTRACTOR=bs4              # bs4|trafilatura|crawl4ai
NAV_FETCH_ENGINE=httpx                 # httpx|crawl4ai
```

Current default recommendation from school `182` benchmark:
- `NAV_FETCH_ENGINE=httpx`
- `NAV_CONTENT_EXTRACTOR=bs4`

Run benchmark for one school with Lite-only models:

```bash
cd backend
scripts/run_school_workflow.sh --school-id 182 --benchmark-lite
```

Optional A/B script for `bs4` vs `trafilatura`:

```bash
cd backend
uv run python scripts/compare_content_extractors.py --city sofia --limit 20 --output reports/extractor_ab.json
```

## License
TBD
