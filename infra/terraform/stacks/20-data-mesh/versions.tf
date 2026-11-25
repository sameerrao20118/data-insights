terraform {
  required_version = ">= 1.10"

  required_providers {
    aws   = { source = "hashicorp/aws", version = "~> 6.70" }
    awscc = { source = "hashicorp/awscc", version = "~> 1.107" }
  }

  cloud {
    workspaces {
      name = "datainsights-20-data-mesh"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.tags
  }
}

provider "awscc" {
  region = var.aws_region
}
