output "origin_certificate" {
  description = "Origin CA certificate for the API host: save as deploy/certs/origin.pem on the server."
  value       = cloudflare_origin_ca_certificate.api.certificate
}

output "origin_certificate_expires_on" {
  value = cloudflare_origin_ca_certificate.api.expires_on
}

output "pages_subdomain" {
  description = "The project's pages.dev hostname."
  value       = cloudflare_pages_project.site.subdomain
}
