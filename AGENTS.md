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

**Current status:** live for Sofia, with no real users yet. Fees come from the fees agent
(`docs/AGENT_FEE_EXTRACTION.md`); the old price extraction stays behind
`EXTRACTION_PIPELINE_PRICES` (off) until it is deleted. Website facts come from the
school-facts stage (`docs/SCHOOL_FACTS.md`). NVO and ДЗИ imports are separate stages.
Routine refreshes run on a schedule (`docs/SCHEDULED_JOBS.md`).

**Status sources:** each feature's doc in `docs/`, `docs/TECH_DEBT_PLAN.md`,
`docs/BACKLOG.md`, git history and the DBs. Agent memory and old notes can be stale, so
check an item is still open before starting it. Update the doc in the same PR that
finishes the item. `docs/archive/` holds finished plans: their status lines are old, but
a backlog item's notes there are still its detail.

**Plans are starting points, not instructions.** Everything in a plan or design doc (an
approach, a tool, a number, the order of steps) was chosen with what was known when it was
written. When you pick an item up, re-evaluate it against current facts and what has been
built since, as if choosing fresh; if a better option fits now, take it and say why it
beats the doc's choice (a big or costly switch gets a second opinion first). This covers
approaches, not Mike's product decisions or the invariants below: if the facts behind one
of those changed, raise it with him rather than overriding it.

Live: the admission points calculator (kindergarten, first grade, and the eighth-grade
score from NVO results) with the catchment map (`docs/ADMISSION_POINTS.md`), and admission
cut-offs (`docs/ADMISSION_CUTOFFS.md`), and today's free places in municipal kindergartens
(`docs/FREE_PLACES.md`). Each school page says what is known about its outdoor space
(`docs/OUTDOOR_SPACE.md`).

---

## Tech Stack

- **Backend:** FastAPI (async), SQLAlchemy 2.0 async ORM, Alembic, PostgreSQL (no PostGIS —
  plain lat/lng floats), Pydantic v2 (also for LLM structured output), **uv** for packages.
- **Frontend:** React 18 + Vite, Leaflet (CartoDB Positron tiles), i18next (BG/EN), Tailwind.
- **Infra:** PostgreSQL + SearXNG (website search) via `docker-compose.yml`.

**uv, not pip:** every backend command is `uv run X`; add dependencies with `uv add`. Never
use `pip install` or `requirements.txt`.

---

## File Structure

Non-obvious places only; list the rest yourself.

- `backend/app/models/` is the schema source of truth; `app/utils/display_gating.py` and
  `website_data.py` hold the publish-boundary gates.
- `backend/app/scrapers/cli.py` is the CLI (`uv run python -m app.scrapers.cli --help`);
  stage runners live in `stages/`, repairs in `repairs/`, validation sections in `validation/`.
- `backend/scripts/` holds the scheduled jobs and `publish_snapshot.sh`.
- `frontend/src/admission/rules/` holds the admission rule sets (JSON, evaluated in the browser).
- `tools/road-graph/` and `tools/transit/` build the drive and transit time data
  (`docs/DRIVE_TIMES.md`, `docs/TRANSIT_TIMES.md`).
- `docs/` holds feature docs and plans; `docs/archive/` holds finished ones.

---

## Skills

Procedures you only need for a specific task. Read the `SKILL.md` when its "use when" applies.

- `skills/github-process/SKILL.md` — PR flow and **all review, merge and publish rules**
  (branch → checks → PR → review by size → self-merge). Use when opening or landing a PR, or
  writing to the launch DB.
- `skills/second-opinion/SKILL.md` — Ask the other vendor's model (Claude ↔ Codex CLI) to
  challenge a big PR, plan, research conclusion or anything hard to undo.
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

### Admission Systems

Rules, calculator and catchments: `docs/ADMISSION_POINTS.md`. Rule sets are JSON files
with worked examples under `frontend/src/admission/rules/`, evaluated in the browser
only. Never change a points value without its source and an example.

**State kindergartens (Sofia):** one points total (address, district, parents' work,
siblings, social and SEN criteria, wish order), random number on ties.
**First grade (municipal schools):** a group 1–4 by catchment and residence, then extra
points inside the group.
**Eighth grade:** a score (бал) per class: weighted НВО results plus two certificate
grades as points (6 = 50, 5 = 39, 4 = 26, 3 = 15), max 500; each class sets its weights.
**Fifth grade, private schools:** no common system.

Cut-offs (last admitted points or score per round) come from kg.sofia.bg and RUO
snapshots, which keep no history of their own; see `docs/ADMISSION_CUTOFFS.md`. Store
aggregates only, never a child's line.

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
- Large files (`app/scrapers/extractor.py` is ~1,900 lines): locate
  code with `grep -n` and read only the needed line ranges.
- **Don't grow the giant files.** New functions do not go into `app/scrapers/cli.py`,
  `app/scrapers/extractor.py`, `app/scrapers/validator.py` or
  `frontend/src/components/SearchPage/SearchPage.jsx`. Put new logic in a focused module
  (a service, a stage module under `app/scrapers/stages/`, the matching extraction module
  under `app/scrapers/extraction/`, a `src/utils/` file) and call it from there; a CLI command
  stays a thin wrapper. Fixing existing code in place is fine. The plan for breaking these
  files up is in `docs/TECH_DEBT_PLAN.md`.

Before implementing, ask: can this be computed instead of stored? Can it go in JSON instead of
a new column? Can I use what's already installed? Is there a simpler way?

---

## GitHub Process and Reviews

Branching, checks, review, merging and publishing are defined in
**`skills/github-process/SKILL.md`**; read it before committing or opening a PR. It is the
single source for those rules, so don't restate them elsewhere. In short:

- Work autonomously: make routine calls yourself and say which default you picked.
- Small PRs get the harness's built-in review; big PRs get a review from the other
  vendor's model (`skills/second-opinion/SKILL.md`). Stop reviewing when a round only finds
  more wording edge cases.
- Merge your own PRs on green CI. A short list (destructive migrations, secrets, infra
  deletions, higher running cost, user privacy, a tripped circuit breaker) still waits
  for Mike.
- Never weaken a check to get your own PR through.
