terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # State of the portal's own footprint. Bucket, key, region and lock table come from a
  # backend config file: terraform init -backend-config=backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "app"        = var.name
      "managed-by" = "terraform"
    }
  }
}
