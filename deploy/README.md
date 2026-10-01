# Production stack (API)

`docker-compose.prod.yml` runs Caddy (HTTPS) → API → Postgres on one host. The frontend is
served separately (Cloudflare Pages) and calls the API at `https://<API_HOST>`. There is
no Redis, Celery or scraping pipeline here: the school data is a published snapshot.

Every command passes the env file explicitly. From the repo root:

```bash
cp deploy/.env.example deploy/.env    # then fill it in; POSTGRES_PASSWORD: openssl rand -hex 32
alias dc='docker compose --env-file deploy/.env -f docker-compose.prod.yml'
```

## First start (order matters)

1. **Start Postgres:** `dc up -d postgres`
2. **Restore the snapshot** into the empty database. The dump carries its own schema and
   `alembic_version`, so do this before any migration:
   `dc exec -T postgres pg_restore -U schools -d schools --no-owner < snapshot.dump`
   (a `pg_dump -Fc` of the launch DB).
3. **Migrate** to the image's schema version: `dc run --rm api alembic upgrade head`
4. **Start the rest:** `dc up -d`
5. **Check:** `curl https://<API_HOST>/ready` returns `{"status":"ready"}`.

Migrations never run on API start. For a later release: `dc pull` (or `dc build`), then
step 3, then step 4.

## Endpoints

- `/health` — liveness: the process answers; no dependencies. The container healthcheck.
- `/ready` — readiness: the DB answers and `schools` has rows. 503 when the DB is down,
  the schema is missing or no snapshot is loaded. Use it for smoke tests and uptime checks.

## Certificates

Production: the `api` DNS record is proxied by Cloudflare with SSL mode Full (strict), and
Caddy serves a Cloudflare Origin CA certificate. Put `origin.pem` and `origin.key` in
`deploy/certs/` (gitignored, mounted read-only) and keep
`CADDY_TLS=/certs/origin.pem /certs/origin.key`.

Local: `API_HOST=api.localhost`, `CADDY_TLS=internal`, and optionally `HTTP_PORT=8081`,
`HTTPS_PORT=8443`. Caddy signs with its own CA, so use `curl -k`:

```bash
curl -k --resolve api.localhost:8443:127.0.0.1 https://api.localhost:8443/ready
```

## Logs

Each container's stdout/stderr is kept by Docker's `json-file` driver, rotated at 10 MB
with 5 compressed files per container. Caddy writes JSON access logs to stdout:
`dc logs -f caddy`.
