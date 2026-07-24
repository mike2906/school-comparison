---
name: common-tasks
description: Step-by-step recipes for routine dev tasks in this repo — add a route/column/translation, seed, run the app, import NVO, print the data-quality scoreboard. Use when performing one of these tasks.
---

# Common Tasks Skill

Recipes for routine tasks. All backend commands use the `uv run` prefix.

## Add a new route
1. Create handler in `app/routers/`
2. Add Pydantic response schema in `app/schemas/`
3. Register router in `app/main.py`
4. Update `.env.example` if new env vars needed

Note the publish boundary (see AGENTS.md → Known Issues): serialize through the display
projections/gates, never raw `attributes`/pricing.

## Add a database column
1. Edit the SQLAlchemy model in `app/models/`
2. Run `uv run alembic revision --autogenerate -m "add column X"`
3. Review the generated migration
4. Run `uv run alembic upgrade head`

ALWAYS autogenerate migrations — never hand-write them.

## Add a translation key
1. Add to `frontend/src/i18n/bg.json`
2. Add matching key to `frontend/src/i18n/en.json`
3. Use in component: `{t('new_key')}`

## Seed the database
```bash
cd backend/scripts/
uv run python seed_data.py
```

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
