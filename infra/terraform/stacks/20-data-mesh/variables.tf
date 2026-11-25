variable "aws_region" {
  type = string
}

variable "project" {
  type    = string
  default = "datainsights"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "tfc_organization" {
  type        = string
  description = "HCP Terraform organization holding the foundation workspace (set by bootstrap)."
}

variable "foundation_workspace" {
  type    = string
  default = "datainsights-10-foundation"
}

variable "enable_smus" {
  type        = bool
  default     = true
  description = "Create the SageMaker Unified Studio domain and projects."
}

variable "smus_owner_arns" {
  type        = list(string)
  default     = []
  description = "IAM role/user ARNs that own every SMUS project -- your console role, so you can open the portal."
}

variable "smus_identity_center_enabled" {
  type    = bool
  default = false
}
