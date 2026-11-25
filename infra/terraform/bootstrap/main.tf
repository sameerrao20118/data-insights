data "tfe_github_app_installation" "this" {
  name = var.github_app_installation_name
}

resource "tfe_project" "this" {
  name        = var.tfc_project
  description = "DataInsights NBA agents on AWS: foundation -> data mesh -> agents."
}

locals {
  # Order matters: each stack reads the previous one's outputs.
  stacks = {
    "10-foundation" = {
      workspace = "datainsights-10-foundation"
      triggers  = ["infra/terraform/stacks/10-foundation/**", "infra/terraform/modules/network/**", "infra/terraform/modules/secure_bucket/**"]
    }
    "20-data-mesh" = {
      workspace = "datainsights-20-data-mesh"
      triggers  = ["infra/terraform/stacks/20-data-mesh/**", "infra/terraform/modules/lake_domain/**", "infra/terraform/modules/smus/**", "infra/terraform/modules/secure_bucket/**", "infra/glue/**", "config/categories.yaml"]
    }
    "30-agents" = {
      workspace = "datainsights-30-agents"
      triggers  = ["infra/terraform/stacks/30-agents/**", "infra/terraform/modules/agent_runtime/**", "infra/terraform/modules/secure_bucket/**"]
    }
  }
}

resource "tfe_workspace" "stack" {
  for_each          = local.stacks
  name              = each.value.workspace
  project_id        = tfe_project.this.id
  description       = "infra/terraform/stacks/${each.key}"
  working_directory = "infra/terraform/stacks/${each.key}"
  terraform_version = var.terraform_version
  trigger_patterns  = each.value.triggers
  queue_all_runs    = false
  tag_names         = ["datainsights", var.environment]

  vcs_repo {
    identifier                 = var.github_repository
    branch                     = var.vcs_branch
    github_app_installation_id = data.tfe_github_app_installation.this.id
  }
}

resource "tfe_workspace_settings" "stack" {
  for_each       = local.stacks
  workspace_id   = tfe_workspace.stack[each.key].id
  execution_mode = "remote"
  auto_apply     = var.auto_apply
  # downstream stacks read upstream outputs (terraform_remote_state)
  remote_state_consumer_ids = {
    "10-foundation" = [tfe_workspace.stack["20-data-mesh"].id, tfe_workspace.stack["30-agents"].id]
    "20-data-mesh"  = [tfe_workspace.stack["30-agents"].id]
    "30-agents"     = []
  }[each.key]
}

# A successful apply upstream queues a plan downstream.
resource "tfe_run_trigger" "chain" {
  for_each      = { "20-data-mesh" = "10-foundation", "30-agents" = "20-data-mesh" }
  workspace_id  = tfe_workspace.stack[each.key].id
  sourceable_id = tfe_workspace.stack[each.value].id
}

# --- variables every workspace gets (project-scoped variable set) ----------

resource "tfe_variable_set" "aws" {
  name        = "${var.tfc_project}-aws"
  description = "Keyless AWS (OIDC) + shared inputs for every DataInsights workspace."
}

resource "tfe_project_variable_set" "aws" {
  project_id      = tfe_project.this.id
  variable_set_id = tfe_variable_set.aws.id
}

locals {
  shared_env = {
    TFC_AWS_PROVIDER_AUTH  = "true"
    TFC_AWS_PLAN_ROLE_ARN  = aws_iam_role.hcp["plan"].arn
    TFC_AWS_APPLY_ROLE_ARN = aws_iam_role.hcp["apply"].arn
  }
  shared_tf = {
    aws_region       = var.aws_region
    environment      = var.environment
    tfc_organization = var.tfc_organization
  }
}

resource "tfe_variable" "shared_env" {
  for_each        = local.shared_env
  variable_set_id = tfe_variable_set.aws.id
  category        = "env"
  key             = each.key
  value           = each.value
}

resource "tfe_variable" "shared_tf" {
  for_each        = local.shared_tf
  variable_set_id = tfe_variable_set.aws.id
  category        = "terraform"
  key             = each.key
  value           = each.value
}

# --- per-workspace inputs ---------------------------------------------------------

locals {
  stack_vars = {
    "10-foundation|lake_admin_arns"     = { value = jsonencode(var.lake_admin_arns), hcl = true }
    "10-foundation|github_repository"   = { value = var.github_repository, hcl = false }
    "10-foundation|github_branch"       = { value = var.vcs_branch, hcl = false }
    "10-foundation|budget_alert_emails" = { value = jsonencode(var.budget_alert_emails), hcl = true }
    "10-foundation|monthly_budget_usd"  = { value = tostring(var.monthly_budget_usd), hcl = true }
    "10-foundation|enable_nat_gateway"  = { value = tostring(var.enable_nat_gateway), hcl = true }
    "20-data-mesh|smus_owner_arns"      = { value = jsonencode(var.lake_admin_arns), hcl = true }
    "20-data-mesh|enable_smus"          = { value = tostring(var.enable_smus), hcl = true }
    "30-agents|bedrock_model_id"        = { value = var.bedrock_model_id, hcl = false }
  }
}

resource "tfe_variable" "stack" {
  for_each     = local.stack_vars
  workspace_id = tfe_workspace.stack[split("|", each.key)[0]].id
  category     = "terraform"
  key          = split("|", each.key)[1]
  value        = each.value.value
  hcl          = each.value.hcl
}

# Owned by CI after the first image push (.github/workflows/agent-image.yml
# updates it through the HCP Terraform API); Terraform only creates it.
resource "tfe_variable" "agent_image_tag" {
  workspace_id = tfe_workspace.stack["30-agents"].id
  category     = "terraform"
  key          = "agent_image_tag"
  value        = "not-built-yet"
  description  = "Set by CI to the pushed git sha. 30-agents fails its plan until an image exists."
  lifecycle {
    ignore_changes = [value]
  }
}
