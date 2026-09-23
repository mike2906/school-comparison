# AGENTS.md — Sofia School Comparison App

**Project instructions for AI coding agents (Claude Code, Codex, and others).**

Read this before making changes. Task-specific procedures live in `skills/` (see below).

---

## Project Overview

**What:** Multi-country school comparison platform for parents to discover, filter, and compare kindergartens and schools.

**Target users:** Parents with children aged 0-18 looking for schools. **MVP: Sofia, Bulgaria.** Expanding to all Bulgarian cities, then other countries.

**Key features:**
- Interactive map showing school locations
- Filter by child's age/enrollment year, city, and school type
- Compare private schools side-by-side (pricing, facilities)
- View NVO exam results for state schools (Bulgaria-specific)
- See admission thresholds (how many points needed to get in)
- Multi-country architecture with country-specific education configs
- Bilingual interface (Bulgarian is primary, English is secondary for MVP)

**Current status:** Multi-country refactoring complete (Phases 1-5). Scraping pipeline Stages 1-7 are implemented and wired into the CLI/Celery pipeline (discovery, website discovery, URL validation, navigation, extraction, validation/spot checks, summarization). Official Bulgaria NVO import is also implemented as an independent stage, with Sofia backfilled for `nvo_4`, `nvo_7`, and `nvo_10` across 2021-2025. Current work is focused on data quality improvements and remaining planned features such as NVO-based parent tools.

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
│   │   ├── routers/      # FastAPI routes (schools.py, compare.py, countries.py)
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

Skills are stored in `skills/` to keep the project model-agnostic. Each is procedural or
reference material you only need for a specific task, so it lives here instead of loading
into every session — read the `SKILL.md` when its "use when" applies.

- `skills/pdf-reader/SKILL.md` — Extract text and tables from PDFs, including fetching PDFs from URLs.
  Use this when a school website links fee PDFs or admissions PDFs and you need to pull structured data.
- `skills/github-process/SKILL.md` — Full PR flow (branch → checks → PR → **manual** `@codex review` → merge).
  Use this when the user says "follow github process" or asks you to open/land a PR.
- `skills/geocoding/SKILL.md` — BG school geocoding: GeoJSON province/municipality naming (СТОЛИЧНА, not
  СОФИЯ), GeoJSON→Nominatim provider tiers, city-match rules. Use when touching `app/services/geocoding`.
- `skills/common-tasks/SKILL.md` — Recipes for routine dev tasks (add route/column/translation, seed, run
  the app, import NVO, data-quality scoreboard). Use when performing one of these.

---

## Database Schema (Core Tables)

`backend/app/models/` is the source of truth; this is the orientation map. Flexible and
localized columns are SQLAlchemy `JSON` (Postgres `json`, not `jsonb`).

### schools
- `country_code` (FK → countries.code), `city` (lowercase ASCII, e.g. `sofia`)
- `name_i18n` (JSON, e.g. `{"bg": "...", "en": "..."}`)
- `school_type`, `education_level` (plain strings; allowed values come from the country's
  `education_config`)
- `summary_i18n` (JSON, AI-generated per language)
- `admission_info` (JSON) — admission system details
- `attributes` (JSON) — flexible school-specific data (internal; see publish boundary)
  - Language focus is an open list of `{language, level}` entries (no hardcoded languages),
    normalized in `app/utils/school_attributes.py`.
- `website_url`, `scrape_status`, `institutional_id` (MoE НЕИСПУО code)

### school_locations (one school can have multiple locations)
- `school_id` (FK → schools.id)
- `address_i18n` (JSON), `lat`, `lng` (plain floats, no PostGIS), `district`, `is_primary`

### location_age_group_shifts (per location × age group)
- `location_id` (FK → school_locations.id) + `age_group` (composite PK)
- `shift` (e.g. morning | afternoon | full_day), `has_organised_groups` (after-school care)

### pricing (for private schools)
- `school_id` (FK), `age_group` (nullable) — prices vary by age
- `category` enum: tuition | food | transport | activities | registration | materials |
  extended_day | uniforms | extracurricular | camp
- `amount` or `amount_min`/`amount_max`, `currency`, `academic_year`, `plan_name`
- `period` enum, **nullable** (null when the school did not state one — never infer it):
  monthly | yearly | one_time | quarter | term | semester
- `source` enum: official | scraped_website | forum | not_found — MUST display in UI
- `source_url`, `pricing_context` (JSON, internal)

### exam_results (NVO scores for state schools)
- `school_id` (FK), `year`, `exam_type` (nvo_4 | nvo_7 | nvo_10)
- `subject`, `metric`, `value`
- Current canonical imported metric: `average_score` (official school-level average scores)

Pipeline/provenance tables: `field_sources`, `source_pages`, `spot_check_results`,
`scrape_log`, `pipeline_runs`, `provider_request_ledger`, `countries`.

**Important:** don't add new columns for one-off fields — put them in the `attributes` /
`admission_info` JSON.

---

## Critical Bulgarian Education Context

### Age Groups Use Calendar Year, Not Exact Age
The system asks: **"What year are you enrolling?"** NOT "How old is your child?"

Formula: `Age Group = Enrollment Year − Birth Year`

Example: A child born Dec 31, 2022 and one born Jan 1, 2022 both enter the same group in Sept 2025.

**Never** use exact birth dates or "age on September 1st" logic. This is a common bug.

### Admission Systems (stored in `admission_info` JSON)

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

This is stored per location and age group in `location_age_group_shifts.shift`.

---

## i18n (Internationalisation) Rules

1. **All UI strings** go through `t('key')` in React or translation dictionaries. NEVER hardcode English or Bulgarian in components.

2. **School names and addresses** are stored per language in `name_i18n` / `address_i18n`
   (`{"bg": ..., "en": ...}`). The frontend displays the version for the user's language.

3. **AI summaries** are stored per language in `summary_i18n`. Frontend picks based on locale.

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

## Geocoding & GeoJSON Data

When working on geocoding (school-location resolution, the composite provider, the EU
GeoJSON dataset), read **`skills/geocoding/SKILL.md`**. Key landmine to remember even
before that: GeoJSON keys schools by **province/municipality** — Sofia is `СТОЛИЧНА`, not
`СОФИЯ` (DB stores `city="sofia"`; the provider normalizes to `СТОЛИЧНА`).

---

## Code Style & Patterns

### KISS Principle (Keep It Simple)
This is a small project (~500 schools, city-scale). Don't over-engineer.

**DO:**
- Use built-in Leaflet distance calculations (not custom haversine formulas)
- Write simple retry logic (not exponential backoff with jitter)
- Store data in the JSON columns when structure varies by school

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

Write tests before implementing these — they encode Bulgarian admission and pricing rules
where a silent off-by-one misleads parents, and the rules are easy to get subtly wrong:
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

## Scope and Review Discipline

- Prefer the smallest change that satisfies the actual requirement.
- Do not generalize an operational or single-use requirement into a reusable production
  subsystem without first explaining the additional complexity and getting approval.
- If a seemingly small task starts expanding substantially in files, lines, abstractions,
  or behavioural scope, stop and reassess the approach before continuing.
- Treat existing implementation work as sunk cost when a simpler design is available.
- Before committing or requesting external/Codex PR review, perform an adversarial
  self-review of the complete diff against the requirement, repository invariants, likely
  edge cases, and existing tests. Fix substantive findings before requesting review.
- Batch related fixes, self-review again, and run the appropriate tests before requesting
  another review. Do not request a new Codex review after every small fix.
- If a PR review finding substantially expands the required design, pause and reconsider
  whether the implementation scope is wrong instead of automatically layering on another
  fix.
- Before requesting `@codex review`, tell Mike that the PR is ready for independent
  ChatGPT pre-review and provide the PR URL. Request Codex Review only when Mike explicitly
  asks.
- Codex Review shares the agents' usage budget: one review per PR unless Mike asks for
  another. When Mike says merge, merge on green CI without requesting a re-review.
- Triage review findings by harm. For extraction heuristics, a finding that only turns a
  would-be value into null (a recall gap) is non-blocking; null is the safe fallback. Stop
  and ask when a second round targets the same heuristic or a fix spawns a new finding.
- Large files (`app/scrapers/extractor_helpers.py`, `extractor.py`, `cli.py` are
  2,000–4,500 lines): locate code with `grep -n` and read only the needed line ranges.

---

## GitHub Process

When the user says **"follow github process"** (or asks you to open/land a PR), follow
**`skills/github-process/SKILL.md`** — the full branch → checks → PR → **manual**
review → merge flow, including how to detect when Codex has finished.

Two rules to keep in mind even before reading it: only commit directly to `main` when the
user explicitly asks, and Codex auto-review is **off**. Provide Mike with the PR URL for
independent ChatGPT pre-review first; post `@codex review` only after Mike explicitly asks.
See its "Usage discipline" section before waiting on reviews/CI or running the full suite.

---

## Common Tasks

Step-by-step recipes for routine tasks (add a route/column/translation, seed, run the app,
import NVO, print the data-quality scoreboard) live in **`skills/common-tasks/SKILL.md`** —
read it when performing one. Two rules that hold regardless: **always autogenerate** Alembic
migrations (never hand-write), and the **NVO import is independent** of the website pipeline
(not part of `all`).

---

## Known Issues & Gotchas

1. **Settings uses factory pattern:** Import `get_settings()`, don't import a `settings` instance directly (unless it's defined in config.py).

2. **uv, not pip:** All commands are `uv run X`. Never suggest `pip install`.

3. **Age filter:** Uses enrollment year and birth year. Never use exact dates or "age on September 1" logic.

4. **NVO import is independent of the website pipeline:** Use the dedicated `nvo` stage when refreshing official exam results; it is not part of `all`.

5. **Publish boundary — API responses are gated (P1.7/P1.15–P1.16):** `schools.attributes`,
   `admission_info`, `pricing_context`, and raw `FieldSource` values are internal storage;
   nothing reaches the wire unless it is declared in the corresponding response allowlist.
   Website-derived fields must additionally pass `app/utils/website_data.py` (publishable
   scrape status and no withholding marker) and the Stage 6 field-level validation gate.
   Useful scraped admission fields are projected through localized attributes; never expose
   `admission_info.website_extracted` or `FieldSource.value_json` directly.
   `app/utils/display_gating.py` drops display fields, pricing rows, and summaries flagged
   by error-level issues / actionable spot-check discrepancies, plus pricing rows with no
   `source_url` or confidence below `PRICING_CONFIDENCE_FLOOR`. When adding a response
   field, go through those projections/gates — never serialize raw JSON or ORM rows directly,
   or you silently bypass the boundary. URL validation failures set a persistent website-data
   withholding marker; only extraction plus deterministic validation may clear it. Gate
   coverage is coupled to the
   validator's spot-check scope (`SPOT_CHECK_CORE_FIELD_PREFIXES`) by a guard test.

---

## API Endpoints

See `backend/app/routers/` (`schools.py`, `compare.py`, `countries.py`) for the current
routes. Every response passes through the publish boundary (Known Issues #5).

---

## What's Next (Planned Features)

- Landing page with filters (route: `/` → `/search` with query params)
- School comparison UI improvements
- NVO results graphs (Recharts)
- Points calculator tool
- Ongoing scraping extraction/validation/summarization quality improvements
- NVO data audits, parent-facing NVO calculator, and admission-threshold tooling

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
SEARXNG_BASE_URL=http://localhost:8080
WEBSITE_SEARCH_PROVIDER_DISABLE_SECONDS=300
URL_VALIDATION_MAX_CONCURRENCY=8
URL_RECOVERY_CONCURRENCY=3
```

---

## Don't Do This

❌ Use `pip install` → Use `uv add` or edit `pyproject.toml`
❌ Hand-write Alembic migrations → Use `alembic revision --autogenerate`
❌ Hardcode "Search" in JSX → Use `{t('search')}`
❌ Add columns for one-off fields → Use the JSON columns (`attributes`, `admission_info`)
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
2. Does this need a new column, or can it go in the `attributes` / `admission_info` JSON?
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
