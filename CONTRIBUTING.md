# Contributing

Use [README.md](README.md) for setup. The seed command is deliberately limited to a local,
disposable `sofia_schools_demo` database. Read [AGENTS.md](AGENTS.md), and the backend or
frontend instructions when working in those directories.

Before submitting a pull request:

```bash
cd backend
uv sync --locked --extra dev
uv run ruff check .
uv run pytest -q
cd ../frontend
npm ci
npm run lint
npm test
npm run build
```

Keep changes narrow, explain the user-visible behavior and include meaningful verification.
Preserve bilingual strings, enrollment-year age groups and the API publication gates. New
schema changes start with Alembic autogeneration and require review. Do not commit datasets,
`.env` files, credentials or private reviews. Report security concerns privately as described
in [SECURITY.md](SECURITY.md).

CI additionally verifies migrations and backup restoration against disposable PostgreSQL.
Deployment, schema and security changes require maintainer review before merge; the detailed
maintainer workflow is in [skills/github-process/SKILL.md](skills/github-process/SKILL.md).
