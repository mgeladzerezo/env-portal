terraform {
  required_version = ">= 1.9.0"
}

locals {
  replicas = {
    small  = 1
    medium = 2
    large  = 4
  }

  tags = {
    "owner"                 = var.owner
    "expires-at"            = var.expires_at
    "portal-environment-id" = var.environment_id
  }
}

# terraform_data is built into Terraform, so this module needs no provider download
# and no credentials. It stands in for real infrastructure in local runs and tests.
resource "terraform_data" "replica" {
  count = local.replicas[var.size]

  input = {
    name = "${var.name}-${count.index}"
    tags = local.tags
  }
}
