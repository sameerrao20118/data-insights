# Run ONCE, from your machine, with your own AWS credentials and an HCP
# Terraform user/team token (`terraform login`). Everything after this runs
# in HCP Terraform with keyless AWS access. See docs/aws_deployment.md.
#
# State: kept in HCP Terraform too, in workspace datainsights-00-bootstrap.
# After the first `terraform init`, set that workspace's Execution Mode to
# "Local" (Settings -> General) so plans run here, with your credentials.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.70" }
    tfe = { source = "hashicorp/tfe", version = "~> 0.82" }
  }

  cloud {
    workspaces {
      name = "datainsights-00-bootstrap"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { project = "datainsights", managed_by = "terraform", stack = "00-bootstrap" }
  }
}

provider "tfe" {
  organization = var.tfc_organization
}
