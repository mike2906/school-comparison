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
- Stage 6: Data validation + monitoring spot-checks
- Stage 7: summarization
- Independent official BG NVO import (`nvo` stage, 2021-2025 Sofia backfill loaded)

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

# Validate extracted data and run monitoring spot-checks (Stage 6)
uv run python -m app.scrapers.cli run --stage validate-data --city sofia --sync --limit 100

# Generate bilingual summaries for eligible schools (Stage 7)
uv run python -m app.scrapers.cli run --stage summarize --city sofia --sync --limit 100

# Import official NVO results (independent of website pipeline)
uv run python -m app.scrapers.cli run --stage nvo --city sofia --country bg --sync
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
EXTRACTION_GENERAL_INFO_MIN_QUALITY_SCORE=4
EXTRACTION_OUTPUT_RETRIES=2
EXTRACTION_OPENROUTER_MODELS=openai/gpt-4o-mini
EXTRACTION_OPENROUTER_PROVIDER_ALLOW_FALLBACKS=true
```

Use the single-school workflow for fast validation:
```bash
cd backend
scripts/run_school_workflow.sh --school-id 182
```

## License
TBD
