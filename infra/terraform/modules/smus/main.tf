# SageMaker Unified Studio (DataZone V2 domain) as the data-mesh control
# plane: one PRODUCER project per data domain, one CONSUMER project for the
# NBA agents, a project profile (Tooling + Lakehouse database) every
# project is created from, and a business glossary of the NBA categories.
#
# What Terraform does NOT do here (docs/aws_deployment.md, step 6): import
# each domain's Glue database into its producer project's catalog, publish
# the assets and subscribe the consumer project. Those need the project
# environments' generated roles, which only exist after this applies --
# datainsights/deploy/smus_catalog.py does it, or the SMUS portal does.
#
# NOT RUN. The blueprint names (Tooling, DataLake) and their regional
# parameter keys follow the SMUS V2 docs as of 2026; a wrong one fails at
# plan time (data source lookup), not silently.
# ref: docs.aws.amazon.com/sagemaker-unified-studio/latest/adminguide/

terraform {
  required_providers {
    aws   = { source = "hashicorp/aws", version = "~> 6.70" }
    awscc = { source = "hashicorp/awscc", version = "~> 1.107" }
  }
}

variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "azs" {
  type = list(string)
}

variable "producer_domains" {
  type        = map(string)
  description = "domain -> Glue database it owns"
}

variable "consumer_project_name" {
  type    = string
  default = "nba-agents"
}

variable "owner_arns" {
  type        = list(string)
  description = "IAM role/user ARNs made owners of every project (your console role)."
}

variable "identity_center_enabled" {
  type    = bool
  default = false
}

variable "glossary_terms" {
  type        = map(string)
  default     = {}
  description = "term -> short description (the NBA categories)."
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

# --- IAM roles SMUS requires ------------------------------------------------

data "aws_iam_policy_document" "datazone_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetContext"]
    principals {
      type        = "Service"
      identifiers = ["datazone.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy" "managed" {
  for_each = {
    domain_execution = "SageMakerStudioDomainExecutionRolePolicy"
    domain_service   = "SageMakerStudioDomainServiceRolePolicy"
    provisioning     = "SageMakerStudioProjectProvisioningRolePolicy"
    glue_access      = "AmazonDataZoneGlueManageAccessRolePolicy"
  }
  name = each.value
}

resource "aws_iam_role" "domain_execution" {
  name               = "${var.name_prefix}-smus-domain-execution"
  assume_role_policy = data.aws_iam_policy_document.datazone_trust.json
}

resource "aws_iam_role_policy_attachment" "domain_execution" {
  role       = aws_iam_role.domain_execution.name
  policy_arn = data.aws_iam_policy.managed["domain_execution"].arn
}

resource "aws_iam_role" "domain_service" {
  name               = "${var.name_prefix}-smus-domain-service"
  assume_role_policy = data.aws_iam_policy_document.datazone_trust.json
}

resource "aws_iam_role_policy_attachment" "domain_service" {
  role       = aws_iam_role.domain_service.name
  policy_arn = data.aws_iam_policy.managed["domain_service"].arn
}

resource "aws_iam_role" "provisioning" {
  name               = "${var.name_prefix}-smus-provisioning"
  assume_role_policy = data.aws_iam_policy_document.datazone_trust.json
}

resource "aws_iam_role_policy_attachment" "provisioning" {
  role       = aws_iam_role.provisioning.name
  policy_arn = data.aws_iam_policy.managed["provisioning"].arn
}

resource "aws_iam_role" "manage_access" {
  name               = "${var.name_prefix}-smus-manage-access"
  assume_role_policy = data.aws_iam_policy_document.datazone_trust.json
}

resource "aws_iam_role_policy_attachment" "manage_access" {
  role       = aws_iam_role.manage_access.name
  policy_arn = data.aws_iam_policy.managed["glue_access"].arn
}

module "bucket" {
  source = "../secure_bucket"
  name   = "${var.name_prefix}-smus-${local.account_id}"
}

# --- the domain ---------------------------------------------------------------

resource "aws_datazone_domain" "this" {
  name                  = "${var.name_prefix}-data-mesh"
  description           = "DataInsights data mesh: producer domains publish curated FDM data products; the NBA agents consume them."
  domain_version        = "V2"
  domain_execution_role = aws_iam_role.domain_execution.arn
  service_role          = aws_iam_role.domain_service.arn

  dynamic "single_sign_on" {
    for_each = var.identity_center_enabled ? [1] : []
    content {
      type            = "IAM_IDC"
      user_assignment = "AUTOMATIC"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.domain_execution,
    aws_iam_role_policy_attachment.domain_service,
  ]
}

# --- blueprints -----------------------------------------------------------------

data "aws_datazone_environment_blueprint" "this" {
  for_each  = toset(["Tooling", "DataLake"])
  domain_id = aws_datazone_domain.this.id
  name      = each.key
  managed   = true
}

resource "aws_datazone_environment_blueprint_configuration" "tooling" {
  domain_id                = aws_datazone_domain.this.id
  environment_blueprint_id = data.aws_datazone_environment_blueprint.this["Tooling"].id
  enabled_regions          = [local.region]
  provisioning_role_arn    = aws_iam_role.provisioning.arn
  manage_access_role_arn   = aws_iam_role.manage_access.arn
  regional_parameters = {
    (local.region) = {
      S3Location = "s3://${module.bucket.id}"
      VpcId      = var.vpc_id
      Subnets    = join(",", var.private_subnet_ids)
      AZs        = join(",", var.azs)
    }
  }
}

resource "aws_datazone_environment_blueprint_configuration" "datalake" {
  domain_id                = aws_datazone_domain.this.id
  environment_blueprint_id = data.aws_datazone_environment_blueprint.this["DataLake"].id
  enabled_regions          = [local.region]
  provisioning_role_arn    = aws_iam_role.provisioning.arn
  manage_access_role_arn   = aws_iam_role.manage_access.arn
  regional_parameters = {
    (local.region) = {
      S3Location = "s3://${module.bucket.id}"
    }
  }
}

# --- project profile: what every mesh project gets ------------------------------

resource "awscc_datazone_project_profile" "mesh" {
  name                   = "data-mesh-project"
  description            = "Tooling (notebooks, query editor) + a Lakehouse database. Producers publish from it; consumers subscribe into it."
  domain_identifier      = aws_datazone_domain.this.id
  domain_unit_identifier = aws_datazone_domain.this.root_domain_unit_id
  status                 = "ENABLED"

  environment_configurations = [
    {
      name                     = "Tooling"
      environment_blueprint_id = data.aws_datazone_environment_blueprint.this["Tooling"].id
      deployment_mode          = "ON_CREATE"
      deployment_order         = 0
      aws_account              = { aws_account_id = local.account_id }
      aws_region               = { region_name = local.region }
    },
    {
      name                     = "Lakehouse Database"
      environment_blueprint_id = data.aws_datazone_environment_blueprint.this["DataLake"].id
      deployment_mode          = "ON_CREATE"
      deployment_order         = 1
      aws_account              = { aws_account_id = local.account_id }
      aws_region               = { region_name = local.region }
    },
  ]

  depends_on = [
    aws_datazone_environment_blueprint_configuration.tooling,
    aws_datazone_environment_blueprint_configuration.datalake,
  ]
}

resource "aws_datazone_policy_grant" "create_projects" {
  domain_identifier = aws_datazone_domain.this.id
  entity_type       = "DOMAIN_UNIT"
  entity_identifier = aws_datazone_domain.this.root_domain_unit_id
  policy_type       = "CREATE_PROJECT_FROM_PROJECT_PROFILE"
  principal {
    user {
      all_users_grant_filter {}
    }
  }
  detail {
    create_project_from_project_profile {
      project_profiles           = [awscc_datazone_project_profile.mesh.project_profile_id]
      include_child_domain_units = true
    }
  }
}

# --- people -------------------------------------------------------------------------

resource "awscc_datazone_user_profile" "owner" {
  for_each          = toset(var.owner_arns)
  domain_identifier = aws_datazone_domain.this.id
  user_identifier   = each.key
  user_type         = can(regex(":role/", each.key)) ? "IAM_ROLE" : "IAM_USER"
  status            = "ASSIGNED"
}

# --- projects: producers + the agent consumer -----------------------------------

locals {
  projects = merge(
    { for d, db in var.producer_domains : d => {
      description = "PRODUCER -- the ${d} domain. Owns Glue database ${db}, its DQ rules, and publishes its tables as data products."
      role        = "producer"
    } },
    { (var.consumer_project_name) = {
      description = "CONSUMER -- the NBA agents. Subscribes to the producer domains' data products; explore here what the AgentCore agents read."
      role        = "consumer"
    } },
  )
  memberships = { for pair in setproduct(keys(local.projects), var.owner_arns) : "${pair[0]}|${pair[1]}" => {
    project = pair[0]
    arn     = pair[1]
  } }
}

resource "awscc_datazone_project" "this" {
  for_each           = local.projects
  name               = replace(each.key, "_", "-")
  description        = each.value.description
  domain_identifier  = aws_datazone_domain.this.id
  domain_unit_id     = aws_datazone_domain.this.root_domain_unit_id
  project_profile_id = awscc_datazone_project_profile.mesh.project_profile_id
  resource_tags = [
    { key = "mesh-role", value = each.value.role },
    { key = "domain", value = each.key },
  ]
  depends_on = [aws_datazone_policy_grant.create_projects]
}

resource "awscc_datazone_project_membership" "owner" {
  for_each           = local.memberships
  domain_identifier  = aws_datazone_domain.this.id
  project_identifier = awscc_datazone_project.this[each.value.project].project_id
  designation        = "PROJECT_OWNER"
  member             = { user_identifier = awscc_datazone_user_profile.owner[each.value.arn].user_profile_id }
}

# --- business glossary: the NBA categories ------------------------------------------

resource "aws_datazone_glossary" "nba" {
  count                     = length(var.glossary_terms) > 0 ? 1 : 0
  domain_identifier         = aws_datazone_domain.this.id
  owning_project_identifier = awscc_datazone_project.this[var.consumer_project_name].project_id
  name                      = "NBA categories"
  status                    = "ENABLED"
}

resource "aws_datazone_glossary_term" "nba" {
  for_each            = length(var.glossary_terms) > 0 ? var.glossary_terms : {}
  domain_identifier   = aws_datazone_domain.this.id
  glossary_identifier = aws_datazone_glossary.nba[0].id
  name                = each.key
  short_description   = each.value
  status              = "ENABLED"
}

output "domain_id" {
  value = aws_datazone_domain.this.id
}

output "portal_url" {
  value = aws_datazone_domain.this.portal_url
}

output "project_ids" {
  value = { for k, p in awscc_datazone_project.this : k => p.project_id }
}

output "project_profile_id" {
  value = awscc_datazone_project_profile.mesh.project_profile_id
}
