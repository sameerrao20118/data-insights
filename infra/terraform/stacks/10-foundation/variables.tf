variable "aws_region" {
  type        = string
  description = "One region for everything. Must offer AgentCore Runtime, SageMaker Unified Studio and your Bedrock model."
}

variable "project" {
  type    = string
  default = "datainsights"
}

variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.environment))
    error_message = "environment: lowercase letters/digits, 2-10 chars (it is part of bucket and role names)."
  }
}

variable "data_domains" {
  type        = list(string)
  default     = ["kernel", "lending", "market_events"]
  description = "Data-mesh domains: every contract `domain:` in config/entities_fdm.yaml, plus market_events (the exogenous event feed). One lake bucket and one LF-tag value each."
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  type    = number
  default = 3
}

variable "enable_nat_gateway" {
  type        = bool
  default     = true
  description = "SMUS spaces/notebooks need outbound internet. ~USD 33/month while it exists."
}

variable "lake_admin_arns" {
  type        = list(string)
  default     = []
  description = "IAM role/user ARNs that administer Lake Formation besides the HCP Terraform apply role -- put your own console role here or you will not see table data in the console."
}

variable "github_repository" {
  type        = string
  default     = ""
  description = "owner/repo allowed to push the agent image via GitHub OIDC. Empty = no CI role."
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "create_github_oidc_provider" {
  type        = bool
  default     = true
  description = "false if the account already has token.actions.githubusercontent.com (one per account)."
}

variable "monthly_budget_usd" {
  type    = number
  default = 150
}

variable "budget_alert_emails" {
  type        = list(string)
  default     = []
  description = "AWS Budgets emails these addresses at 80% forecast / 100% actual. Empty = budget with no alerts."
}
