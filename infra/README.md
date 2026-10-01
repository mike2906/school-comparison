# Infrastructure (Terraform)

Terraform manages the Cloudflare side of production: the `api` and site DNS records, TLS
mode, the API's Origin CA certificate, the Pages project and its domain, the `www`
redirect and the crawler settings. It does not manage the API host: the OVH provider can
order a VPS, but a changed image reinstalls (wipes) it and the SSH key needs an image ID
that only exists after the first order. The host is ordered by hand and set up by
`deploy/bootstrap.sh`. Email Routing's records are not declared here, so Terraform leaves
`contact@` alone.

Monthly cost: everything here is on Cloudflare's free plan. The only paid item is the VPS
(OVH VPS-1: 2 vCPU, 4 GB, 40 GB; EUR 4.49 ex VAT, monthly, no commitment; 2026-10-01).

## One-time setup (human)

1. **API host.** Order an OVH VPS-1 (monthly, Ubuntu 24.04, your SSH public key). Note its
   IPv4 address. Then set it up and deploy the stack: `deploy/README.md`.
2. **Terraform 1.16** on your machine.
3. **HCP Terraform** (free plan): create an organization and a workspace named
   `schooldecider`, and set the workspace's execution mode to **Local**. Runs then happen
   on your machine and HCP stores only the state. Run `terraform login`; it saves its own
   token in `~/.terraform.d/credentials.tfrc.json`.
4. **Cloudflare API token** (My Profile → API Tokens → Create Custom Token), limited to
   the `schooldecider.com` zone and your account:
   - Zone: Zone Read, DNS Write, Zone Settings Write, SSL and Certificates Write, Bot
     Management Write, Single Redirect Write
   - Account: Cloudflare Pages Write

   A token with the same items as Read is enough for `terraform plan`.
5. **Local files, outside the repo** (`mkdir -p ~/.config/schooldecider && chmod 700 ~/.config/schooldecider`):
   - `~/.config/schooldecider/terraform.env`, mode 600:
     ```
     export CLOUDFLARE_API_TOKEN=...
     export TF_CLOUD_ORGANIZATION=<your HCP organization>
     ```
   - the Origin CA key and its signing request (only the request is read by Terraform):
     ```bash
     cd ~/.config/schooldecider && openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -keyout origin.key -out origin.csr -subj "/CN=api.schooldecider.com" && chmod 600 origin.key
     ```
6. `cp terraform.tfvars.example terraform.tfvars` and fill in the account ID, zone ID
   (both on the zone's Overview page) and the VPS address. Not secrets, but gitignored.

## Run

```bash
cd infra
source ~/.config/schooldecider/terraform.env
terraform init
terraform plan
terraform apply
```

Then put the certificate on the server, next to the key:

```bash
terraform output -raw origin_certificate > ~/.config/schooldecider/origin.pem
scp ~/.config/schooldecider/origin.pem ~/.config/schooldecider/origin.key ubuntu@<ip>:schooldecider/deploy/certs/
```

After the first apply, check in the dashboard:
- AI Crawl Control: **Bot Preference Sync is on** (the provider cannot set
  `bot_preference_sync_enabled` yet, cloudflare/terraform-provider-cloudflare#7385) and
  shows Search allow, Agent allow, Training disallow.
- `https://schooldecider.com/robots.txt` (once the site is deployed) starts with
  Cloudflare's block of training-crawler `Disallow` lines, followed by ours.

`terraform plan` fails if Cloudflare's published IPv4 ranges differ from
`deploy/cloudflare-proxies.caddy`. Update that file, then on the server reinstall it and
restart the firewall and Caddy (`deploy/README.md`).

## Crawler settings

| Policy (frontend `robots.txt`) | `cloudflare_bot_management` |
|---|---|
| search: yes | `aisearch = "disabled"` (not blocked) |
| AI input (assistants, agents): yes | `ai_user = "disabled"` (not blocked) |
| AI training: no, by request only | `ai_training = "disallow"` (robots.txt lines, no edge block) |
| no crawler blocked at the edge | `ai_bots_protection`, `crawler_protection` = `"disabled"` |
| builds and uptime checks reach the API | `fight_mode = false` |

`is_robots_txt_managed` is the older managed-robots.txt switch, replaced by Bot Preference
Sync in September 2026; it is left unset.

## Switching to schooldecider.bg

1. Add the `.bg` zone to Cloudflare; set `site_host` and `zone_id` in `terraform.tfvars`.
   The API certificate is reissued for `api.schooldecider.bg` (make a new signing request
   with that name first; if the plan does not replace the certificate, add
   `-replace=cloudflare_origin_ca_certificate.api`) and must be copied to the server again.
2. Server `deploy/.env`: `API_HOST`, `ALLOWED_ORIGINS`.
3. Frontend build (CD): `VITE_SITE_ORIGIN`, `VITE_API_BASE`, `PRERENDER_API_URL`, then
   rebuild: canonical, `hreflang` and sitemap URLs all come from `VITE_SITE_ORIGIN`.
   `frontend/.env.example` and `DEFAULT_SITE_ORIGIN` in `frontend/src/utils/languageUrl.js`
   name the domain too.
4. Keep the `.com` zone (Email Routing) and add a `.com` → `.bg` redirect rule there. This
   config manages one zone, so that needs a second small set of resources.
5. The API token must cover the new zone. Re-verify Search Console and Bing, resubmit the
   sitemap, and update the CARTO key's domain restriction.
