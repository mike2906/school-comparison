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
# Databases
docker-compose up -d

# Backend
cd backend
uv run uvicorn app.main:app --reload

# Frontend
cd frontend
npm run dev
```

## License
TBD
