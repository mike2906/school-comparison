variable "account_id" {
  description = "Cloudflare account ID."
  type        = string
}

variable "zone_id" {
  description = "Cloudflare zone ID of site_host. The zone already exists (Registrar) and is not managed here."
  type        = string
}

variable "site_host" {
  description = "Public hostname of the site; the API is api.<site_host>. Changing it is the switch to schooldecider.bg (see infra/README.md)."
  type        = string
  default     = "schooldecider.com"
}

variable "api_ipv4" {
  description = "Public IPv4 address of the API host (the OVH VPS, ordered by hand)."
  type        = string
}

variable "origin_csr_path" {
  description = "Certificate signing request for the API's Origin CA certificate. The key it was made from stays outside the repo and never reaches Terraform."
  type        = string
  default     = "~/.config/schooldecider/origin.csr"
}

variable "pages_project_name" {
  description = "Cloudflare Pages project name; also its <name>.pages.dev subdomain."
  type        = string
  default     = "schooldecider"
}
