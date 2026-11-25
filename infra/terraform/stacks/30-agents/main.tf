# Layer 3 -- the agents. Consumes the foundation (registry, KMS, Athena)
# and the data mesh (Glue databases, LF-tags, event feed) and runs the NBA
# agent container on AgentCore Runtime.

data "terraform_remote_state" "foundation" {
  backend = "remote"
  config = {
    organization = var.tfc_organization
    workspaces   = { name = var.foundation_workspace }
  }
}

data "terraform_remote_state" "mesh" {
  backend = "remote"
  config = {
    organization = var.tfc_organization
    workspaces   = { name = var.data_mesh_workspace }
  }
}

locals {
  f = data.terraform_remote_state.foundation.outputs
  m = data.terraform_remote_state.mesh.outputs
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
    stack       = "30-agents"
  }
}

# Fails the PLAN, with a clear message, if CI has not pushed this tag yet.
data "aws_ecr_image" "agent" {
  repository_name = local.f.ecr_repository.name
  image_tag       = var.agent_image_tag
}

module "agent" {
  source = "../../modules/agent_runtime"

  name_prefix           = local.f.name_prefix
  image_uri             = "${local.f.ecr_repository.url}@${data.aws_ecr_image.agent.image_digest}"
  ecr_repository_arn    = local.f.ecr_repository.arn
  kms_key_arn           = local.f.kms_key_arn
  bedrock_model_id      = var.bedrock_model_id
  athena_workgroup      = local.f.athena_workgroup
  athena_results_bucket = local.f.athena_results_bucket
  glue_databases        = local.m.glue_databases
  read_domains          = local.m.table_domains
  lf_tag_domain_key     = local.m.lf_tag_domain_key
  lake_bucket_arns      = [for d, b in local.f.lake_buckets : b.arn]
  events_s3_uri         = local.m.events_s3_uri
}

resource "aws_ssm_parameter" "discovery" {
  for_each = {
    "agent/runtime_arn"     = module.agent.runtime_arn
    "agent/insights_bucket" = module.agent.insights_bucket
    "agent/ledger_table"    = module.agent.ledger_table
  }
  name  = "/${var.project}/${var.environment}/${each.key}"
  type  = "String"
  value = each.value
}
