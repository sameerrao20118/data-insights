# Layer 1 -- the platform foundation every later stack builds on.
# Nothing here knows about agents or SMUS; it is the self-serve platform
# the data-mesh domains and the agent runtime sit on.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
    stack       = "10-foundation"
  }
}

module "network" {
  source             = "../../modules/network"
  name               = local.name
  cidr               = var.vpc_cidr
  az_count           = var.az_count
  enable_nat_gateway = var.enable_nat_gateway
}

# --- encryption ---------------------------------------------------------------

resource "aws_kms_key" "lake" {
  description             = "${local.name} data lake, ledger and agent outputs"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${local.name}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

# --- one lake bucket per data-mesh domain ------------------------------------
# Layout (written by datainsights/deploy/publish_raw.py and the Glue job):
#   s3://<bucket>/raw/<table>/<table>.csv        producer drop zone
#   s3://<bucket>/curated/<table>/part-*.parquet  the domain's data product

module "lake" {
  source      = "../../modules/secure_bucket"
  for_each    = toset(var.data_domains)
  name        = "${local.name}-${replace(each.key, "_", "-")}-${local.account_id}"
  kms_key_arn = aws_kms_key.lake.arn
  tags        = { domain = each.key, data-product = "${each.key}-curated" }
}

module "artifacts" {
  source      = "../../modules/secure_bucket"
  name        = "${local.name}-artifacts-${local.account_id}"
  kms_key_arn = aws_kms_key.lake.arn
}

module "athena_results" {
  source                             = "../../modules/secure_bucket"
  name                               = "${local.name}-athena-results-${local.account_id}"
  kms_key_arn                        = aws_kms_key.lake.arn
  noncurrent_version_expiration_days = 1
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results_expiry" {
  bucket = module.athena_results.id
  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
  }
}

# --- query engine -------------------------------------------------------------

resource "aws_athena_workgroup" "agents" {
  name          = "${local.name}-agents"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    # Cost guard: no single agent query may scan more than 1 GB.
    bytes_scanned_cutoff_per_query = 1073741824
    result_configuration {
      output_location = "s3://${module.athena_results.id}/agents/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
    engine_version {
      selected_engine_version = "Athena engine version 3"
    }
  }
}

# --- agent image registry -----------------------------------------------------

resource "aws_ecr_repository" "agent" {
  name                 = "${local.name}/nba-agent"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.lake.arn
  }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# --- Lake Formation: federated governance -------------------------------------
# ACCOUNT-WIDE SETTING. This replaces the account's data lake admins and
# turns off the IAMAllowedPrincipals default for NEW databases/tables, so
# access to the lake is Lake Formation grants only (LF-tags below, SMUS
# subscriptions). If this account already uses Lake Formation, review
# before applying.

resource "aws_lakeformation_data_lake_settings" "this" {
  admins = distinct(concat([data.aws_iam_session_context.current.issuer_arn], var.lake_admin_arns))
  parameters = {
    CROSS_ACCOUNT_VERSION = "4"
  }
}

resource "aws_lakeformation_lf_tag" "domain" {
  key        = "domain"
  values     = var.data_domains
  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_lf_tag" "data_product" {
  key        = "data_product"
  values     = ["curated", "raw"]
  depends_on = [aws_lakeformation_data_lake_settings.this]
}

# --- cost guard -----------------------------------------------------------------

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$${var.project}"]
  }

  dynamic "notification" {
    for_each = length(var.budget_alert_emails) > 0 ? [["FORECASTED", 80], ["ACTUAL", 100]] : []
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value[0]
      threshold                  = notification.value[1]
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}
