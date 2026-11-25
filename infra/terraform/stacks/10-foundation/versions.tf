terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.70" }
  }

  # HCP Terraform, VCS-driven (infra/terraform/bootstrap creates this
  # workspace). Organization comes from TF_CLOUD_ORGANIZATION.
  cloud {
    workspaces {
      name = "datainsights-10-foundation"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.tags
  }
}
