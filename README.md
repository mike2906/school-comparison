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

## License
TBD
