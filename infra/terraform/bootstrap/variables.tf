variable "tfc_organization" {
  type        = string
  description = "Your HCP Terraform organization (app.terraform.io/app/<this>)."
}

variable "tfc_project" {
  type    = string
  default = "datainsights"
}

variable "github_repository" {
  type        = string
  description = "owner/repo of this repository on GitHub."
}

variable "github_app_installation_name" {
  type        = string
  description = "GitHub account/org where the HCP Terraform GitHub App is installed (usually the repo owner)."
}

variable "vcs_branch" {
  type    = string
  default = "main"
}

variable "terraform_version" {
  type    = string
  default = "~> 1.14.0"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "auto_apply" {
  type        = bool
  default     = false
  description = "false = every apply waits for a human Confirm in HCP Terraform (production-like)."
}

# --- per-stack inputs, set here once ----------------------------------------

variable "lake_admin_arns" {
  type        = list(string)
  description = "Your console role/user ARN(s): Lake Formation admin + SMUS project owner."
}

variable "bedrock_model_id" {
  type        = string
  description = "The one Bedrock model/inference-profile id the agents may call."
}

variable "budget_alert_emails" {
  type    = list(string)
  default = []
}

variable "monthly_budget_usd" {
  type    = number
  default = 150
}

variable "enable_nat_gateway" {
  type    = bool
  default = true
}

variable "enable_smus" {
  type    = bool
  default = true
}
