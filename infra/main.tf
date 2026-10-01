# Cloudflare side of production: DNS, TLS, the Pages project and crawler settings. The API
# host is ordered by hand and set up by deploy/bootstrap.sh. Runbook: infra/README.md

locals {
  api_host = "api.${var.site_host}"
  www_host = "www.${var.site_host}"

  # The ranges Caddy trusts and the host firewall admits on 443.
  origin_allowed_ipv4 = regexall("\\d+\\.\\d+\\.\\d+\\.\\d+/\\d+", file("${path.module}/../deploy/cloudflare-proxies.caddy"))
}

# --- API: proxied record, strict TLS to the origin --------------------------------------

resource "cloudflare_dns_record" "api" {
  zone_id = var.zone_id
  name    = local.api_host
  type    = "A"
  content = var.api_ipv4
  proxied = true
  ttl     = 1

  lifecycle {
    precondition {
      condition     = toset(local.origin_allowed_ipv4) == toset(data.cloudflare_ip_ranges.current.ipv4_cidrs)
      error_message = "Cloudflare's IPv4 ranges differ from deploy/cloudflare-proxies.caddy. Update the file, then on the server reinstall it and restart schooldecider-firewall and Caddy."
    }
  }
}

resource "cloudflare_zone_setting" "ssl" {
  zone_id    = var.zone_id
  setting_id = "ssl"
  value      = "strict"
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = var.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

# The certificate Caddy serves to Cloudflare. Public; the key is not in state.
resource "cloudflare_origin_ca_certificate" "api" {
  csr                = file(pathexpand(var.origin_csr_path))
  hostnames          = [local.api_host]
  request_type       = "origin-ecc"
  requested_validity = 5475

  # A changed request would replace (and revoke) the certificate Caddy is serving. Reissue
  # on purpose: terraform apply -replace=cloudflare_origin_ca_certificate.api
  lifecycle {
    ignore_changes = [csr]
  }
}

data "cloudflare_ip_ranges" "current" {}

# --- Site: Pages project (Direct Upload; CD builds and uploads) -------------------------

resource "cloudflare_pages_project" "site" {
  account_id        = var.account_id
  name              = var.pages_project_name
  production_branch = "main"
}

resource "cloudflare_pages_domain" "site" {
  account_id   = var.account_id
  project_name = cloudflare_pages_project.site.name
  name         = var.site_host
}

# The API does not create this record (the dashboard does). It follows the domain
# association: a CNAME to Pages without one answers 522.
resource "cloudflare_dns_record" "site" {
  zone_id = var.zone_id
  name    = var.site_host
  type    = "CNAME"
  content = cloudflare_pages_project.site.subdomain
  proxied = true
  ttl     = 1

  depends_on = [cloudflare_pages_domain.site]
}

# www: a proxied placeholder record (never contacted) and a redirect to the apex.
resource "cloudflare_dns_record" "www" {
  zone_id = var.zone_id
  name    = local.www_host
  type    = "AAAA"
  content = "100::"
  proxied = true
  ttl     = 1
}

resource "cloudflare_ruleset" "redirects" {
  zone_id = var.zone_id
  name    = "Redirects"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [
    {
      description = "www to apex"
      expression  = "(http.host eq \"${local.www_host}\")"
      action      = "redirect"
      action_parameters = {
        from_value = {
          status_code           = 301
          preserve_query_string = true
          target_url = {
            expression = "concat(\"https://${var.site_host}\", http.request.uri.path)"
          }
        }
      }
    }
  ]
}

# --- Crawlers ---------------------------------------------------------------------------
# Policy (frontend robots.txt): search yes, AI input yes, AI training no, and no crawler is
# blocked at the edge. `ai_training = "disallow"` is the robots.txt-only choice; Bot
# Preference Sync publishes it as per-crawler Disallow lines ahead of our robots.txt.
# Not set here: `bot_preference_sync_enabled` (the provider cannot manage it yet,
# cloudflare/terraform-provider-cloudflare#7385; turn it on in the dashboard).
# `is_robots_txt_managed` is the older switch it replaced; the provider defaults it to false.
resource "cloudflare_bot_management" "crawlers" {
  zone_id = var.zone_id

  ai_training = "disallow"
  aisearch    = "disabled"
  ai_user     = "disabled"

  is_robots_txt_managed = false
  ai_bots_protection    = "disabled"
  crawler_protection    = "disabled"
  # Off so the Pages build and uptime checks can call the API unchallenged.
  fight_mode = false
}
