# Production stack (API)

`docker-compose.prod.yml` runs Caddy (HTTPS) → API → Postgres on one host. The frontend is
served separately (Cloudflare Pages) and calls the API at `https://<API_HOST>`. There is
no Redis, Celery or scraping pipeline here: the school data is a published snapshot.

Every command passes the env file explicitly. From the repo root:

```bash
cp deploy/.env.example deploy/.env    # then fill it in; POSTGRES_PASSWORD: openssl rand -hex 32
alias dc='docker compose --env-file deploy/.env -f docker-compose.prod.yml'
```

Once CD has deployed, the running image is recorded in `deploy/release.env` (written by
`deploy/deploy.sh`) and the alias on the host must read it too, or `dc up` goes back to the
hand-built image:

```bash
alias dc='docker compose --env-file deploy/.env --env-file deploy/release.env -f docker-compose.prod.yml'
```

## Host setup (once)

The host is an OVH VPS (Ubuntu 26.04) reached as `ubuntu@<ip>` with your SSH key.
`bootstrap.sh` installs Docker, turns off SSH password logins, enables unattended
security updates and installs the firewall service. It is safe to run again.

```bash
scp deploy/bootstrap.sh deploy/firewall.sh deploy/cloudflare-proxies.caddy ubuntu@<ip>:
ssh ubuntu@<ip> sudo bash bootstrap.sh
```

Then put the code in `~/schooldecider` and continue below. The repo is private, so send a
copy instead of cloning. For the first start, build the image on the host (`dc build api`);
after that CD delivers images (see Continuous deployment):

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

Migrations never run on API start. Later releases are deployed by CD, which runs step 3
and step 4 with the new image.

## Continuous deployment

`.github/workflows/deploy.yml` runs on every push to `main`: tests first, then a release, unless
the changes since the commit the live site was built from (`version.txt`) are only docs,
skills or Terraform.

1. **API** (only when `backend/`, `deploy/`, the compose file or the workflow changed): the
   image is built on the runner and pushed to `ghcr.io/mike2906/school-comparison-api:<commit>`
   (private). The runner then calls the host over SSH. Its key can run one command there,
   `deploy/deploy.sh`, which pulls the image with the job's own short-lived token (never
   stored on the host), runs `alembic upgrade head`, restarts the API and waits for
   `/ready`. If the new container is not ready within a minute it restarts the previous
   image. `deploy/smoke.sh api` then checks the API from outside.
2. **Site**: `npm run build:production` against the live API, upload to the Pages project
   with wrangler, then `deploy/smoke.sh site`. If that fails, Pages is rolled back to the
   deployment that was live before.
3. **One release.** If the site step fails after this run deployed the API, the API also
   goes back to the previous image.

The host keeps two release images, the running one and the rollback target
(`deploy/release.env`), and removes older ones after each successful deploy.

**Migrations and rollback.** A rollback changes the image only; migrations stay applied
and there is no automatic downgrade or backup. So every migration must leave the schema
usable by the previous image (expand first, contract in a later release): add columns and
tables, do not drop or rename what the running code reads. A migration that cannot meet
this is deployed by hand, with a `pg_dump` first, and its PR says so.

**After a data publish** (a new snapshot restored on the host), rebuild the site: Actions →
Deploy → Run workflow → `frontend_only`. Or `gh workflow run deploy.yml -f frontend_only=true`.
It refuses to run when the API is behind the commit (a failed or rolled-back release): run
a full release instead (the same, without `frontend_only`).

**What CD does not change.** `docker-compose.prod.yml`, `deploy/Caddyfile`,
`deploy/cloudflare-proxies.caddy` and `deploy/deploy.sh` itself. A commit that changes one
of them is refused by the host until you sync it:

```bash
git archive --format=tar main | ssh ubuntu@<ip> 'tar -x -C ~/schooldecider'
ssh ubuntu@<ip> 'sudo install -m 0755 ~/schooldecider/deploy/deploy.sh /usr/local/sbin/schooldecider-deploy'
# then, if the compose file or a Caddy file changed: dc up -d
```

Then rerun the failed workflow. By hand on the host, `schooldecider-deploy rollback <commit>`
goes back to the previous image if the last deploy moved the host to `<commit>`.

### One-time setup (human)

No command below prints a secret.

1. **Deploy key**, without a passphrase, used by nothing else:
   ```bash
   ssh-keygen -t ed25519 -N '' -C schooldecider-cd -f ~/.config/schooldecider/cd_deploy_key
   ```
2. **Host:** sync the code and install the script (the two commands above), then allow the
   key to run only that script (no shell, no forwarding, no file copy):
   ```bash
   printf 'restrict,command="/usr/local/sbin/schooldecider-deploy" %s\n' "$(cat ~/.config/schooldecider/cd_deploy_key.pub)" | ssh ubuntu@<ip> 'cat >> ~/.ssh/authorized_keys'
   ssh -i ~/.config/schooldecider/cd_deploy_key -o IdentitiesOnly=yes ubuntu@<ip> id   # prints the script's usage line, not your uid
   ```
3. **Cloudflare token** for Pages only (My Profile → API Tokens → Create Custom Token):
   Account → Cloudflare Pages → Edit, account resources limited to this account, no zone
   permissions. This is a different token from Terraform's.
4. **GitHub** (repository → Settings → Secrets and variables → Actions, or `gh`):
   ```bash
   gh secret set DEPLOY_SSH_KEY < ~/.config/schooldecider/cd_deploy_key
   gh secret set CLOUDFLARE_PAGES_TOKEN        # paste the token at the prompt
   gh secret set DEPLOY_HOST --body <ip>       # a secret so the address is masked in run logs
   gh variable set CLOUDFLARE_ACCOUNT_ID --body <account id>
   gh variable set VITE_CARTO_API_KEY --body <key>   # optional; public, restricted at CARTO
   ```
   GHCR needs nothing: the workflow uses its own `GITHUB_TOKEN` (`packages: write` to
   build, `packages: read` to deploy).

The host's SSH public key is pinned in `deploy/ssh_host_key.pub`; the runner refuses any
other. If the host is reinstalled, replace it (`ssh-keyscan -t ed25519 <ip>`, key type and
key only, checked against the host's console).

The two secrets are repository secrets, so a workflow on any branch pushed to this
repository can read them. On a GitHub plan with environments for private repositories
(Pro or higher), move them to a `production` environment limited to `main` and add
`environment: production` to the `deploy-api`, `frontend` and `rollback-api` jobs.

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
