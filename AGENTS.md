# AGENTS.md — Sofia School Comparison App

**Context file for AI coding assistants (Kilo, Aider, Cursor, etc.)**

This file provides everything you need to work effectively on this project. Read it before making any changes.

---

## Project Overview

**What:** Bilingual (Bulgarian/English) web app for parents in Sofia to discover, filter, and compare kindergartens and schools.

**Target users:** Parents with children aged 0-18 looking for schools in Sofia, Bulgaria.

**Key features:**
- Interactive map showing school locations
- Filter by child's age/enrollment year
- Compare private schools side-by-side (pricing, facilities)
- View NVO exam results for state schools
- See admission thresholds (how many points needed to get in)
- Bilingual interface (Bulgarian is primary, English is secondary)

**Current status:** Phase 1 scaffold complete. Backend API runs, frontend displays map, 10 schools seeded. Ready for feature development.

---

## Tech Stack & Constraints

### Backend (Python)
- **FastAPI** (async) — REST API
- **SQLAlchemy 2.0** (async ORM) — database models
- **Alembic** — migrations (ALWAYS autogenerate, never hand-write)
- **PostgreSQL** — database (no PostGIS, just lat/lng floats)
- **Pydantic v2** — validation & LLM structured output
- **uv** — package manager (NEVER use pip or requirements.txt)

**Critical:** All backend commands use `uv run` prefix. Example: `uv run pytest`, `uv run alembic upgrade head`.

### Frontend (JavaScript/React)
- **React 18 + Vite** — UI framework
- **Leaflet.js** — map (uses CartoDB Positron tiles for clean look)
- **i18next** — translations (BG/EN)
- **Tailwind CSS** — styling

### Database
- **PostgreSQL** via Docker
- Connection: `postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools`
- Redis: `redis://localhost:6379/0` (for Celery, not critical yet)

---

## File Structure

```
sofia-school-compare/
├── backend/
│   ├── app/
│   │   ├── models/       # SQLAlchemy ORM (school.py, pricing.py, exam_results.py)
│   │   ├── schemas/      # Pydantic request/response models
│   │   ├── routers/      # FastAPI routes (schools.py, compare.py)
│   │   ├── services/     # Business logic
│   │   ├── config.py     # Settings (uses get_settings() factory)
│   │   ├── database.py   # DB engine, session
│   │   └── main.py       # FastAPI app
│   ├── tests/            # pytest tests
│   ├── alembic/          # DB migrations
│   ├── pyproject.toml    # Dependencies (managed by uv)
│   └── scripts/
│       └── seed_data.py  # Seed script (10 realistic Sofia schools)
├── frontend/
│   ├── src/
│   │   ├── components/   # React components (Map/, SchoolCard/, etc.)
│   │   ├── i18n/         # bg.json, en.json
│   │   ├── api/          # API client wrappers
│   │   └── utils/        # Helper functions (education.js)
│   └── package.json
└── docker-compose.yml    # PostgreSQL + Redis
```

---

## Project Skills (Repo-Local)

Skills are stored in `skills/` to keep the project model-agnostic.

- `skills/pdf-reader/SKILL.md` — Extract text and tables from PDFs, including fetching PDFs from URLs.
  Use this when a school website links fee PDFs or admissions PDFs and you need to pull structured data.

---

## Database Schema (Core Tables)

### schools
- `id` (PK)
- `name` (Bulgarian)
- `name_en` (English/transliterated, nullable)
- `school_type` (state | private | international)
- `education_level` (nursery | kindergarten | primary | lower_secondary | upper_secondary)
- `summary_bg`, `summary_en` (AI-generated)
- `admission_info` (JSONB) — admission system details
- `attributes` (JSONB) — flexible school-specific data
  - **Note (planned scraping model):** Language focus should be modeled as a structured list in `attributes`, e.g. `{ language: string, level: enum }`, with **open language list** (no hardcoded languages). PydanticAI will enforce the schema during scraping.

### school_locations (one school can have multiple locations)
- `id` (PK)
- `school_id` (FK → schools.id)
- `age_group` (nursery | first | second | third | preschool | grade_1_4 | grade_5_7 | grade_8_12)
- `address`, `address_en`, `lat`, `lng`
- `shift` (morning | afternoon | full_day)
- `has_organised_groups` (boolean) — after-school care

### pricing (for private schools)
- `id` (PK)
- `school_id` (FK → schools.id)
- `age_group` (nullable) — prices vary by age
- `category` (tuition | food | transport | activities)
- `amount`, `currency`, `period` (monthly | yearly | one_time)
- `source` (official | scraped_website | forum | not_found) — MUST display in UI

### exam_results (NVO scores for state schools)
- `school_id` (FK)
- `year`, `exam_type` (nvo_4 | nvo_7 | nvo_10)
- `subject`, `metric`, `value`

**Important:** `admission_info` and `attributes` are JSONB. Don't add new columns for one-off fields — put them in JSONB.

---

## Critical Bulgarian Education Context

### Age Groups Use Calendar Year, Not Exact Age
The system asks: **"What year are you enrolling?"** NOT "How old is your child?"

Formula: `Age Group = Enrollment Year − Birth Year`

Example: A child born Dec 31, 2022 and one born Jan 1, 2022 both enter the same group in Sept 2025.

**Never** use exact birth dates or "age on September 1st" logic. This is a common bug.

### Admission Systems (stored in `admission_info` JSONB)

**State kindergartens:** Points system (Бал)
- Parents get points based on: address, employment, siblings, etc.
- Historical thresholds stored per round: `{"year": 2024, "age_group": "first", "rounds": [{"round": 1, "last_admitted_points": 8, "admitted_count": 35}]}`
- This data comes from kg.sofia.bg

**State gymnasiums:** NVO score formula
- `score = NVO Bulgarian + NVO Math + Subject 1 + Subject 2`
- Historical minimums: `{"year": 2024, "min_score": 196.5}`

**Private schools:** Entry tests/interviews (no standardised system)

### Shifts (смени)
Many state schools run two shifts:
- Morning: 7:30-13:00 (usually younger kids)
- Afternoon: 13:30-19:00 (usually older kids)

This is stored in `school_locations.shift`.

---

## i18n (Internationalisation) Rules

1. **All UI strings** go through `t('key')` in React or translation dictionaries. NEVER hardcode English or Bulgarian in components.

2. **School names, addresses** are stored in both Bulgarian and English (`name_en`, `address_en`). The frontend displays the appropriate version based on the user's language preference.

3. **AI summaries** are stored in both languages (`summary_bg`, `summary_en`). Frontend picks based on locale.

4. **Domain-specific terms** have dedicated keys:
   - НВО → NVO
   - Бал → Points
   - Смена → Shift
   - Организирани групи → After-school care

5. Language preference stored in `localStorage` under key `language`. Default: Bulgarian.

**Bad:**
```jsx
<button>Search Schools</button>
```

**Good:**
```jsx
<button>{t('search_schools')}</button>
```

---

## Code Style & Patterns

### KISS Principle (Keep It Simple)
This is a small project (~500 schools, city-scale). Don't over-engineer.

**DO:**
- Use built-in Leaflet distance calculations (not custom haversine formulas)
- Write simple retry logic (not exponential backoff with jitter)
- Store data in JSONB when structure varies by school

**DON'T:**
- Build for global scale we don't have
- Add caching/queues before there's a performance problem
- Write custom implementations of things libraries already do

### Backend Patterns

**Settings:**
```python
from app.config import get_settings

settings = get_settings()  # Factory pattern, returns singleton
db_url = settings.DATABASE_URL
```

**Database session:**
```python
from app.database import get_db

async def my_route(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(School))
```

**Migrations:**
ALWAYS autogenerate:
```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

Never hand-write migrations except to add `CREATE EXTENSION` (not needed for this project).

### Frontend Patterns

**API calls:**
```javascript
import { fetchSchools } from '../api/schools';

const schools = await fetchSchools({ age_group: 'first' });
```

**Translations:**
```javascript
import { useTranslation } from 'react-i18next';

const { t, i18n } = useTranslation();
return <h1>{t('welcome')}</h1>;
```

**Map tiles (CartoDB Positron for clean look):**
```jsx
<TileLayer
  url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
  attribution='&copy; OpenStreetMap contributors &copy; CARTO'
/>
```

---

## Testing Requirements

**CRITICAL:** These features MUST have tests before implementation:
- Admission points calculator (every criterion, edge cases)
- NVO score formula
- Age → group mapping (Target Admission Year logic)
- Price extraction validation

**Don't need tests yet:**
- Simple CRUD routes
- UI components

**Run tests:**
```bash
cd backend
uv run pytest
uv run pytest tests/test_api.py -v
```

---

## Common Tasks

### Add a new route
1. Create handler in `app/routers/`
2. Add Pydantic response schema in `app/schemas/`
3. Register router in `app/main.py`
4. Update `.env.example` if new env vars needed

### Add a database column
1. Edit the SQLAlchemy model in `app/models/`
2. Run `uv run alembic revision --autogenerate -m "add column X"`
3. Review the generated migration
4. Run `uv run alembic upgrade head`

### Add a translation key
1. Add to `frontend/src/i18n/bg.json`
2. Add matching key to `frontend/src/i18n/en.json`
3. Use in component: `{t('new_key')}`

### Seed the database
```bash
cd backend/scripts/
uv run python seed_data.py
```

### Run the app
```bash
# Terminal 1: Databases
docker-compose up -d

# Terminal 2: Backend
cd backend
uv run uvicorn app.main:app --reload

# Terminal 3: Frontend
cd frontend
npm run dev
```

---

## Known Issues & Gotchas

1. **Foreign keys must be explicit in SQLAlchemy models:**
   ```python
   school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
   ```
   Not just `Integer`. SQLAlchemy needs the `ForeignKey` declaration.

2. **Settings uses factory pattern:** Import `get_settings()`, don't import a `settings` instance directly (unless it's defined in config.py).

3. **uv, not pip:** All commands are `uv run X`. Never suggest `pip install`.

4. **Alembic migrations:** First migration does NOT need `CREATE EXTENSION postgis` — we're using simple lat/lng floats, not geometry types.

5. **Age filter:** Uses enrollment year and birth year. Never use exact dates or "age on September 1" logic.

6. **School names and addresses:** Stored in both Bulgarian and English (`name_en`, `address_en`). The frontend displays the appropriate version based on the user's language preference.

---

## API Endpoints (Current)

```
GET  /schools              # List schools (filter by age_group, type)
GET  /schools/{id}         # Single school detail
GET  /compare?ids=1,2,3    # Compare multiple schools
```

Response includes nested `locations`, `pricing`, `exam_results`.

---

## What's Next (Planned Features)

- Landing page with filters (route: `/` → `/search` with query params)
- School comparison UI improvements
- NVO results graphs (Recharts)
- Points calculator tool
- Scraping pipeline (Phase 2+)

---

## Environment Variables

Required in `backend/.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools
REDIS_URL=redis://localhost:6379/0
```

Optional (for later phases):
```
OPENROUTER_API_KEY=  # For AI features
```

---

## Don't Do This

❌ Use `pip install` → Use `uv add` or edit `pyproject.toml`
❌ Hand-write Alembic migrations → Use `alembic revision --autogenerate`
❌ Hardcode "Search" in JSX → Use `{t('search')}`
❌ Add columns for one-off fields → Use JSONB (`attributes`, `admission_info`)
❌ Use earth curvature formulas → Use Leaflet's built-in distance
❌ Build complex retry logic → Simple 1-2-3 retries is fine

---

## Key Files to Check Before Changing

- `app/config.py` — Settings pattern
- `app/database.py` — Session management
- `app/models/school.py` — Core schema
- `frontend/src/i18n/` — All translations
- `backend/pyproject.toml` — Dependencies

---

## Questions to Ask Yourself

Before implementing a feature:
1. Does this need to be in the database, or can it be computed on the fly?
2. Does this need a new column, or can it go in JSONB?
3. Does this need a library, or can I use what's already installed?
4. Am I building for scale I don't have?
5. Is there a simpler way?

When in doubt: **start simple, add complexity only when you hit a real problem.**

---

## Getting Help

- Database schema: Check `app/models/`
- API routes: Check `app/routers/`
- Translations: Check `frontend/src/i18n/`
- Seeded data: Run `backend/scripts/seed_data.py` to see realistic examples

This file should give you everything you need. If you're unsure about something, check existing code first — the patterns are already there.
