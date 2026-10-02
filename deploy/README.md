# Production stack (API)

`docker-compose.prod.yml` runs Caddy (HTTPS) → API → Postgres on one host. The frontend is
served separately (Cloudflare Pages) and calls the API at `https://<API_HOST>`. There is
no Redis, Celery or scraping pipeline here: the school data is a published snapshot.

Every command passes the env file explicitly. From the repo root:

```bash
cp deploy/.env.example deploy/.env    # then fill it in; POSTGRES_PASSWORD: openssl rand -hex 32
alias dc='docker compose --env-file deploy/.env -f docker-compose.prod.yml'
```

## Host setup (once)

The host is an OVH VPS (Ubuntu 26.04) reached as `ubuntu@<ip>` with your SSH key.
`bootstrap.sh` installs Docker, turns off SSH password logins, enables unattended
security updates and installs the firewall service. It is safe to run again.

```bash
scp deploy/bootstrap.sh deploy/firewall.sh deploy/cloudflare-proxies.caddy ubuntu@<ip>:
ssh ubuntu@<ip> sudo bash bootstrap.sh
```

Then put the code in `~/schooldecider` and continue below. The repo is private, so until
CD exists send a copy instead of cloning, and build the image on the host (`dc build api`):

```bash
git archive --format=tar main | ssh ubuntu@<ip> 'mkdir -p ~/schooldecider && tar -x -C ~/schooldecider'
```

**Firewall.** `schooldecider-firewall.service` runs `firewall.sh` at boot (before Docker starts) and
whenever Docker restarts: port 443 accepts only Cloudflare's IPv4 ranges
(`cloudflare-proxies.caddy`), port 80 is closed, SSH stays open (keys only). The compose
file publishes Caddy's ports on IPv4 only, so nothing is reachable over IPv6, which the
firewall does not cover. Docker requires the firewall
service: if the rules cannot be applied, Docker does not start and the API is down
rather than exposed (`systemctl status schooldecider-firewall`). When Cloudflare's ranges change
(`terraform plan` in `infra/` fails), update the file, then:

```bash
scp deploy/cloudflare-proxies.caddy ubuntu@<ip>:
ssh ubuntu@<ip> 'sudo install -m 0644 cloudflare-proxies.caddy /etc/schooldecider/ && sudo systemctl restart schooldecider-firewall'
# and, after pulling the repo on the host: dc restart caddy
```

Check from any machine that is not Cloudflare: `curl -m 5 -k https://<ip>/` must time out.

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
`CADDY_TLS=/certs/origin.pem /certs/origin.key`. The certificate comes from Terraform and
the key never leaves your machine and the server: `infra/README.md`.

Local: `API_HOST=api.localhost`, `CADDY_TLS=internal`, and optionally `HTTP_PORT=8081`,
`HTTPS_PORT=8443`. Caddy signs with its own CA, so use `curl -k`:

```bash
curl -k --resolve api.localhost:8443:127.0.0.1 https://api.localhost:8443/ready
```

## Logs

Each container's stdout/stderr is kept by Docker's `json-file` driver, rotated at 10 MB
with 5 compressed files per container. Caddy writes JSON access logs to stdout:
`dc logs -f caddy`. Caddy takes the visitor's address from Cloudflare's `CF-Connecting-IP`
header (only on connections from a Cloudflare range) and passes it to the API, so
`client_ip` in Caddy's log and the address in the API's log are the visitor's;
`remote_ip` is Cloudflare's.
