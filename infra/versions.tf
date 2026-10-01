terraform {
  required_version = "~> 1.16"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.26"
    }
  }

  # State lives in HCP Terraform. The organization comes from TF_CLOUD_ORGANIZATION. The
  # workspace must use local execution: runs happen on your machine, so the Cloudflare
  # token and the certificate request never leave it and HCP only stores state.
  cloud {
    workspaces {
      name = "schooldecider"
    }
  }
}

# Reads CLOUDFLARE_API_TOKEN from the environment.
provider "cloudflare" {}
