# API image: FastAPI + Alembic. Build context is backend/ (see .dockerignore).
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies only: the app runs from /app/app, so the project itself is never installed.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project


FROM python:3.12-slim-bookworm

RUN useradd --uid 10001 --create-home app
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# Only Caddy can reach this port, so its X-Forwarded-* headers are trusted.
# Two workers by default, one per vCPU of the VPS (OVH VPS-1). Set here so it ships with
# the image through CD; API_WORKERS overrides it (e.g. `docker compose run -e`). The
# WEB_CONCURRENCY that docker-compose.prod.yml passes is overridden by --workers. Each
# worker keeps its own response caches (routers/schools.py).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*' --workers \"${API_WORKERS:-2}\""]
