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

Not implemented yet:
- Stage 5+: extraction, data validation, summarization

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
```

## License
TBD
