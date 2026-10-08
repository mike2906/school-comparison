---
name: common-tasks
description: Step-by-step recipes for routine dev tasks in this repo — add a route/column/translation, seed, run the app, import NVO, print the data-quality scoreboard, publish data to production. Use when performing one of these tasks.
---

# Common Tasks Skill

Recipes for routine tasks. All backend commands use the `uv run` prefix.

## Add a new route
1. Create handler in `app/routers/`
2. Add Pydantic response schema in `app/schemas/`
3. Register router in `app/main.py`
4. Update `.env.example` if new env vars needed

Note the publish boundary (see `backend/AGENTS.md` → Publish Boundary): serialize through the display
projections/gates, never raw `attributes`/pricing.

## Add a database column
1. Edit the SQLAlchemy model in `app/models/`
2. Run `uv run alembic revision --autogenerate -m "add column X"`
3. Review the generated migration
4. Run `uv run alembic upgrade head`

Start from autogenerate. Add hand-written operations only where autogenerate can't express
the change (data moves, Postgres enum values, renames), and verify upgrade/downgrade locally
before applying it anywhere shared.

## Add a translation key
1. Add to `frontend/src/i18n/bg.json`
2. Add matching key to `frontend/src/i18n/en.json`
3. Use in component: `{t('new_key')}`

## Seed the database
```bash
# Create this disposable database once, from the repository root:
docker compose exec -T postgres createdb -U postgres sofia_schools_demo
cd backend
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools_demo
uv run alembic upgrade head
uv run python -m scripts.seed_data --reset-demo-data
```

Seeding replaces existing demo data. The script refuses other database names, remote
hosts and connection query overrides. Never point this recipe at the launch database.

## Run the app
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

## Import official NVO results
```bash
cd backend
uv run python -m app.scrapers.cli run --stage nvo --sync --city sofia --country bg
```

Optional filters:
- `--year 2025` for a single year
- `--exam-type nvo_7` (repeatable)
- `--school-id 123` for a targeted import

NVO import is independent of the website pipeline — it is not part of `all`.

## Publish data to production

Production serves a copy of the launch DB. A change there (import, correction, merge) is
not on the site until it is published:

```bash
backend/scripts/backup_offsite.sh        # uploads the launch DB; the output names the dump
gh workflow run deploy.yml -f snapshot=sofia_schools-<stamp>.dump
```

The run loads the dump on the host (backup first, automatic restore on failure) and rebuilds
the site; the API is down for about a minute. Wait for it in one background command, then
check the run summary (`schools: <before> -> <after>`) and the live API against the launch DB
(e.g. the school count from `https://api.schooldecider.com/schools?country_code=bg&city=sofia`).
A green run alone is not proof: verify the data. Details and the manual fallback:
`deploy/README.md` → Data publish.

Publish only launch-DB changes that were themselves allowed (the one-off data-fix exception in
`skills/github-process/SKILL.md`, or approved by Mike), and report each publish to Mike with
the dump name and the before/after counts. A publish whose diff you cannot explain goes to Mike.

## Data-quality scoreboard
```bash
cd backend
uv run python -m app.scrapers.cli data-quality --city sofia --country bg
```
Prints the six go-live quality metrics + recent pipeline runs. Every batch `run`
(extract/validate-data/summarize/all/…) also records a `pipeline_runs` row with a
`metrics` snapshot via `app/services/pipeline_runs.py`.

## Promote a manually curated English identity

Use an explicit school cohort. The command is a dry-run unless `--commit` is supplied:

```bash
cd backend
uv run python -m app.scrapers.cli promote-curated-identities \
  --school-id 521 --city sofia --country bg --promoted-by codex

# After reviewing the candidate and decision:
uv run python -m app.scrapers.cli promote-curated-identities \
  --school-id 521 --city sofia --country bg --promoted-by codex --commit
```

The command requires the existing two-signal corroboration, manual-review metadata, at
least two source URLs on the official website domain, and no conflicting canonical EN
name. It promotes only `name_i18n.en` and records durable provenance; missing Stage 6
validation continues to withhold all other website-derived fields. Repeat `--school-id`
for a bounded multi-school cohort.
