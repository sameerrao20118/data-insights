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
  type = string
}

variable "foundation_workspace" {
  type    = string
  default = "datainsights-10-foundation"
}

variable "data_mesh_workspace" {
  type    = string
  default = "datainsights-20-data-mesh"
}

variable "bedrock_model_id" {
  type        = string
  description = "The ONE model the agents may call -- a Bedrock model id or cross-region inference profile id (e.g. us.<provider>.<model>). List yours: aws bedrock list-inference-profiles."
  validation {
    condition     = length(var.bedrock_model_id) > 0
    error_message = "bedrock_model_id is required (no default on purpose: choosing a paid model is a decision)."
  }
}

variable "agent_image_tag" {
  type        = string
  description = "Tag in the foundation ECR repo. Set by .github/workflows/agent-image.yml after each push."
  validation {
    condition     = length(var.agent_image_tag) > 0 && var.agent_image_tag != "latest"
    error_message = "Pin an immutable tag (the git sha the workflow pushed), never latest."
  }
}
