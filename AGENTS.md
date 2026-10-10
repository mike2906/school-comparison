# AGENTS.md — Sofia School Comparison App

Project instructions for any AI coding agent or harness.

## How these instructions are organised

- **This file** — always-on context: product, domain rules, repo-wide invariants.
- **`backend/AGENTS.md`** — schema, backend patterns, migrations, tests, publish boundary.
  **Read it before changing anything under `backend/`.**
- **`frontend/AGENTS.md`** — i18n rules and frontend patterns.
  **Read it before changing anything under `frontend/`.**
- **`skills/<name>/SKILL.md`** — task procedures, read on demand (index below).

Some harnesses load the nested `AGENTS.md` files and skills automatically; others don't.
Either way, read them when the pointers above apply. Tool-specific files (`CLAUDE.md`,
`.claude/skills`, `.agents/skills`) are pointers only — never put content in them; edit
`AGENTS.md` or `skills/` instead.

---

## Project Overview

**What:** Multi-country school comparison platform for parents to discover, filter, and
compare kindergartens and schools.

**Target users:** Parents with children aged 0-18. **MVP: Sofia, Bulgaria.** Expanding to all
Bulgarian cities, then other countries.

**Key features:**
- Interactive map showing school locations
- Filter by enrollment year, city, and school type
- Compare private schools side-by-side (pricing, facilities)
- NVO exam results for state schools (Bulgaria-specific)
- Admission thresholds (how many points needed to get in)
- Multi-country architecture with country-specific education configs
- Bilingual interface (Bulgarian primary, English secondary for MVP)

**Current status:** Multi-country refactoring complete. Scraping pipeline Stages 1-7 are
implemented and wired into the CLI/Celery pipeline (discovery, website discovery, URL
validation, navigation, extraction, validation/spot checks, summarization). Official Bulgaria
NVO import is an independent stage, with Sofia backfilled for `nvo_4`, `nvo_7`, `nvo_10`
across 2021-2025. Current work is data quality and go-live (backlog: `docs/GO_LIVE_PLAN.md`).

**Status source of truth:** `docs/GO_LIVE_PLAN.md` checkboxes, plus git history and the DBs.
Agent memory or notes can be stale; verify an item is still open before starting it, and
tick the item in the plan in the same PR that finishes it.

Planned, not yet built: admission points calculator, parent-facing NVO calculator, and
admission-threshold tooling.

---

## Tech Stack

- **Backend:** FastAPI (async), SQLAlchemy 2.0 async ORM, Alembic, PostgreSQL (no PostGIS —
  plain lat/lng floats), Pydantic v2 (also for LLM structured output), **uv** for packages.
- **Frontend:** React 18 + Vite, Leaflet (CartoDB Positron tiles), i18next (BG/EN), Tailwind.
- **Infra:** PostgreSQL + Redis via `docker-compose.yml` (Redis is for Celery).

**uv, not pip:** every backend command is `uv run X`; add dependencies with `uv add`. Never
use `pip install` or `requirements.txt`.

---

## File Structure

```
├── AGENTS.md             # this file
├── backend/
│   ├── AGENTS.md         # backend rules (read before backend work)
│   ├── app/
│   │   ├── models/       # SQLAlchemy ORM — schema source of truth
│   │   ├── schemas/      # Pydantic request/response models
│   │   ├── routers/      # FastAPI routes (schools.py, compare.py, countries.py)
│   │   ├── services/     # Business logic (incl. geocoding/)
│   │   ├── scrapers/     # Scraping pipeline + CLI (cli.py)
│   │   ├── utils/        # Incl. publish-boundary gates (display_gating.py, website_data.py)
│   │   ├── config.py     # Settings (get_settings() factory)
│   │   ├── database.py   # DB engine, session
│   │   └── main.py       # FastAPI app
│   ├── tests/            # pytest
│   ├── alembic/          # DB migrations
│   ├── scripts/          # Seed and one-off maintenance scripts
│   ├── .env.example      # All env vars, with defaults
│   └── pyproject.toml    # Dependencies (uv)
├── frontend/
│   ├── AGENTS.md         # frontend rules (read before frontend work)
│   └── src/              # components/, i18n/, api/, utils/
├── skills/               # Task procedures (index below)
├── docs/                 # Plans and design notes (GO_LIVE_PLAN.md)
└── docker-compose.yml    # PostgreSQL + Redis
```

---

## Skills

Procedures you only need for a specific task. Read the `SKILL.md` when its "use when" applies.

- `skills/github-process/SKILL.md` — Full PR flow and **all review rules** (branch → checks →
  PR → pre-review → optional `@codex review` → merge). Use when the user says "follow github
  process" or asks you to open/land a PR.
- `skills/common-tasks/SKILL.md` — Recipes: add route/column/translation, seed, run the app,
  import NVO, data-quality scoreboard, promote curated identities, publish data to
  production. Use when doing one of these.
- `skills/geocoding/SKILL.md` — BG geocoding: GeoJSON province naming (СТОЛИЧНА, not СОФИЯ),
  GeoJSON→Nominatim tiers, city-match rules. Use when touching `app/services/geocoding`.
- `skills/pdf-reader/SKILL.md` — Extract text/tables from PDFs (files, URLs, linked from a
  page). Use when a school website links fee or admissions PDFs.

---

## Bulgarian Education Context

### Age Groups Use Calendar Year, Not Exact Age
The system asks **"What year are you enrolling?"**, NOT "How old is your child?"

Formula: `Age Group = Enrollment Year − Birth Year`

Example: children born Dec 31, 2022 and Jan 1, 2022 both enter the same group in Sept 2025.

**Never** use exact birth dates or "age on September 1st" logic. This is a common bug.

### Admission Systems (stored in `admission_info` JSON)

**State kindergartens:** Points system (Бал)
- Parents get points based on: address, employment, siblings, etc.
- Historical thresholds per round: `{"year": 2024, "age_group": "first", "rounds": [{"round": 1, "last_admitted_points": 8, "admitted_count": 35}]}`
- This data comes from kg.sofia.bg

**State gymnasiums:** NVO score formula
- `score = NVO Bulgarian + NVO Math + Subject 1 + Subject 2`
- Historical minimums: `{"year": 2024, "min_score": 196.5}`

**Private schools:** Entry tests/interviews (no standardised system)

### Shifts (смени)
Many state schools run two shifts:
- Morning: 7:30-13:00 (usually younger kids)
- Afternoon: 13:30-19:00 (usually older kids)

Stored per location and age group in `location_age_group_shifts.shift`.

### Domain terms (dedicated i18n keys)
НВО → NVO · Бал → Points · Смена → Shift · Занималня → After-school care

---

## Repo-Wide Invariants

1. **Publish boundary.** Internal storage (`schools.attributes`, `admission_info`,
   `pricing_context`, raw `FieldSource` values) never reaches an API response except through
   the response allowlists and gates. Details in `backend/AGENTS.md`; read them before adding
   or changing any response field.
2. **NVO import is independent** of the website pipeline: use the dedicated `nvo` stage; it
   is not part of `all`.
3. **Production serves a published copy of the launch DB.** A launch-DB change is not live
   until it is published (`skills/common-tasks/SKILL.md` → Publish data to production); say
   so when reporting a data fix.
4. **JSON over columns:** put one-off or per-school fields in the `attributes` /
   `admission_info` JSON, not new columns.
5. **Migrations start from autogenerate** and are reviewed and verified before applying
   (details in `backend/AGENTS.md`).
6. **All UI strings go through i18n.** Never hardcode English or Bulgarian in components.

---

## Scope Discipline

This is a small project (~500 schools, city-scale). Start simple; add complexity only when
you hit a real problem.

- Prefer the smallest change that satisfies the actual requirement.
- Do not generalize an operational or single-use requirement into a reusable production
  subsystem without first explaining the added complexity and getting approval.
- If a seemingly small task starts expanding substantially in files, lines, abstractions, or
  behavioural scope, stop and reassess the approach before continuing.
- Treat existing implementation work as sunk cost when a simpler design is available.
- Prefer what libraries already do (Leaflet distance, not a custom haversine) and simple
  retries (1-2-3, not backoff with jitter). No caching/queues before a real performance problem.
- Large files (`app/scrapers/extractor_helpers.py`, `extractor.py`, `cli.py` are
  2,000–4,500 lines): locate code with `grep -n` and read only the needed line ranges.

Before implementing, ask: can this be computed instead of stored? Can it go in JSON instead of
a new column? Can I use what's already installed? Is there a simpler way?

---

## GitHub Process and Reviews

Branching, checks, self-review, PRs, review requests, triage, and merging are defined in
**`skills/github-process/SKILL.md`**; read it before committing or opening a PR. It is the
single source for the review rules, so don't restate them elsewhere. Hold these even before
reading it:

- Commit directly to `main` only when the user explicitly asks.
- Codex auto-review is **off**. Give Mike the PR URL for independent ChatGPT pre-review;
  post `@codex review` only when Mike explicitly asks, and only one Codex review per PR.
- When Mike says **merge**, merge on green CI without requesting another review.
- Agents may merge their own PRs only within the self-merge lane defined in the skill.
