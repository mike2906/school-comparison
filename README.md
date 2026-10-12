# School Comparison

[![CI](https://github.com/mike2906/school-comparison/actions/workflows/ci.yml/badge.svg)](https://github.com/mike2906/school-comparison/actions/workflows/ci.yml)

A bilingual (Bulgarian/English) web app that helps parents in Sofia find and compare
kindergartens and schools. Live at [schooldecider.com](https://schooldecider.com).

This repository holds the code, infrastructure and agent instructions. The school dataset
is not in it.

See [contributing](CONTRIBUTING.md), [security reporting](SECURITY.md) and the
[publication checklist](docs/PUBLICATION_CHECKLIST.md) for verification and operational limits.

## What it does

- Map and list of schools, filtered by enrollment year, city and school type.
- Side-by-side comparison of private schools: fees, facilities, languages.
- Official NVO exam results for state schools (grades 4, 7 and 10, 2021–2025).
- Every published price links to the page it was read from.
- One prerendered, indexable page per school, in both languages.

## Architecture

The [architecture guide](ARCHITECTURE.md) explains extraction, evidence gates and tradeoffs.

```
Browser ──> Cloudflare Pages   static site: React 18 + Vite, prerendered per school
        └─> Cloudflare proxy ──> OVH VPS: Caddy ──> FastAPI ──> PostgreSQL
```

| Part | Stack | Where |
|---|---|---|
| API | FastAPI (async), SQLAlchemy 2.0, Alembic, Pydantic v2 | `backend/app/` |
| Frontend | React 18, Vite, Leaflet, i18next, Tailwind | `frontend/` |
| Scraping pipeline | Click CLI, Crawl4AI, pydantic-ai | `backend/app/scrapers/` |
| Production stack | Docker Compose: Caddy, API, Postgres on one VPS | `docker-compose.prod.yml`, `deploy/` |
| Cloud config | Terraform for Cloudflare: DNS, TLS, Origin CA certificate, Pages | `infra/` |
| CI and CD | GitHub Actions | `.github/workflows/` |

Production serves a published snapshot of the data. The scraping pipeline runs
offline and is not part of the production stack. The host firewall accepts
HTTPS only from Cloudflare's address ranges.

### Continuous deployment

Every push to `main` runs the tests and then a release
([`deploy.yml`](.github/workflows/deploy.yml), runbook in [`deploy/README.md`](deploy/README.md)):

1. The API image is built and pushed to GHCR. The runner's SSH key can run one command on
   the host, `deploy/deploy.sh`, which pulls the image, runs `alembic upgrade head`,
   restarts the API and waits for `/ready`. If the new container is not ready within a
   minute, the previous image is restarted.
2. The site is built against the live API and uploaded to Cloudflare Pages, then smoke
   tested. If that fails, Pages goes back to the previous deployment, and so does the API
   if this run deployed it.

A rollback changes the image only, so migrations are written expand-then-contract: each
one must leave the schema usable by the previous image.

## Publish boundary

Scraped data is wrong often enough that storing a value and publishing it are separate
steps. Extracted values, provenance and validation reports live in internal JSON columns,
and no API response serializes them directly:

- Each response is built from an explicit allowlist of fields.
- Website-derived fields are published only when the school's scrape status is
  publishable, no withholding marker is set, and a current validation report exists.
- `backend/app/utils/display_gating.py` drops fields, price rows and summaries that carry
  an error-level validation issue or an actionable spot-check discrepancy.
- A price row needs a source URL and a minimum confidence. Its amount is checked against
  the text of the linked page; a deposit or a single installment filed as tuition is
  rejected.

The gates fail closed: a value that cannot be verified is withheld, not shown with a
caveat. The prerendered site is built from the public API, so it cannot show anything the
boundary withholds.

## Scraping and validation pipeline

Stages run from one CLI (`uv run python -m app.scrapers.cli run --stage <stage>`):

1. **Discovery**: schools from the Ministry of Education registry and Sofia's
   kindergarten admission system.
2. **Website discovery**: find each school's official site and filter out directory sites.
3. **URL validation**: heuristics, with an optional LLM fallback.
4. **Navigation**: crawl and cache the relevant pages.
5. **Extraction**: fees and general information as structured LLM output (Pydantic
   schemas), skipped when the page hash is unchanged.
6. **Validation**: deterministic checks plus LLM spot checks on a sample. The results
   feed the publish boundary.
7. **Summaries**: short bilingual descriptions.

NVO exam results are imported separately from the official open-data sets. Each billable
LLM request is recorded in a ledger, and a run stops at its cost cap.

## Built with AI agents

Most of the code was written by coding agents (Claude Code and Codex) working under rules
kept in the repository:

- [`AGENTS.md`](AGENTS.md), [`backend/AGENTS.md`](backend/AGENTS.md) and
  [`frontend/AGENTS.md`](frontend/AGENTS.md) hold the domain rules and invariants an agent
  reads before changing code. [`skills/`](skills/) holds task procedures.
- [`skills/github-process/SKILL.md`](skills/github-process/SKILL.md) defines the review
  rules. Work happens on an `agent/*` branch and goes through a pull request and CI. The
  agent reviews its own complete diff, and code changes get a second, tool-assisted review.
- An agent may merge its own pull request only when the change is docs, tests, config
  defaults or a small bug fix with a regression test. Changes to the publish boundary,
  migrations, credentials, infrastructure, running cost or the checks themselves go to a
  human.
- An agent must never loosen a check to get its pull request through.

[`docs/GO_LIVE_PLAN.md`](docs/GO_LIVE_PLAN.md) is the working backlog, including what
went wrong along the way and how it was fixed.

## Known limitations

- A few modules are much too large: `backend/app/scrapers/cli.py` and
  `extractor_helpers.py` are over 4,000 lines each, and the main search page component is
  about 2,000.
- The backend has no type checker in CI yet; both backend (ruff) and frontend (ESLint) run a linter.
- Coverage is Sofia only, and a fee is published only where the gates can verify it, so
  many private schools show no prices.

## Local development

```bash
docker compose up -d                              # PostgreSQL, SearXNG
docker compose exec -T postgres createdb -U postgres sofia_schools_demo

cd backend
cp .env.example .env
sed -i 's|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools_demo|' .env
uv sync --locked --extra dev
uv run alembic upgrade head
uv run python -m scripts.seed_data --reset-demo-data  # replaces demo data only
uv run uvicorn app.main:app --reload

cd ../frontend
npm ci
npm run dev
```

The demo database URL is saved in `backend/.env`, so a new backend terminal uses the same
database. Shell-level `DATABASE_URL` overrides still take precedence. The demo database is
disposable. Reseeding replaces its school data; the script refuses
the launch database and remote targets. Development service ports bind to loopback.
Create the demo database once; on later starts, skip `createdb` and seeding unless you
want to replace its data. Production setup is separate in [`deploy/README.md`](deploy/README.md).

Tests: `cd backend && uv run pytest -q` (about 1,500 tests, on SQLite) and
`cd frontend && npm test`.

More recipes are in [`skills/common-tasks/SKILL.md`](skills/common-tasks/SKILL.md).

`backend/tests/golden_corpus/` holds excerpts of public school web pages, used as
regression fixtures for extraction. The text belongs to the schools and is not covered by
the licence below. Names of staff, parents and pupils, personal email addresses and mobile
numbers in it are replaced with made-up ones. To have an excerpt removed, write to
contact@schooldecider.com.

`backend/data/bg/education.geojson` is the Bulgaria file of Eurostat GISCO's
[education services dataset](https://gisco-services.ec.europa.eu/pub/education/), whose
listed source is the Ministry of Education and Science of Bulgaria. GISCO's
[metadata](https://gisco-services.ec.europa.eu/pub/education/metadata.pdf) refers to each
national provider for licence terms and states none for Bulgaria, so the file is not
covered by the licence below either.

## License

[MIT](LICENSE)
