# Layer 2 -- the data mesh. Each contract domain becomes a producer-owned
# data product (Glue database + contract-typed tables + DQ rules + a
# curation job), Lake Formation is the only access path, and SageMaker
# Unified Studio is where the domains publish and the agents' consumer
# project subscribes.

data "terraform_remote_state" "foundation" {
  backend = "remote"
  config = {
    organization = var.tfc_organization
    workspaces = {
      name = var.foundation_workspace
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  f    = data.terraform_remote_state.foundation.outputs
  name = local.f.name_prefix
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
    stack       = "20-data-mesh"
  }

  # ONE source of truth: config/entities_fdm.yaml -> lake_tables.json
  # (python -m datainsights.deploy.lake_contract; CI fails if stale).
  spec           = jsondecode(file("${path.module}/lake_tables.json"))
  table_domains  = toset(local.spec.domains)
  tables_by_dom  = { for d in local.table_domains : d => [for t in local.spec.tables : t if t.domain == d] }
  events_domain  = "market_events"
  glossary_terms = { for k, v in yamldecode(file("${path.module}/../../../../config/categories.yaml")).categories : v.label => v.description }
}

check "contract_domains_have_lake_buckets" {
  assert {
    condition     = length(setsubtract(local.table_domains, toset(local.f.data_domains))) == 0
    error_message = "lake_tables.json names a domain with no lake bucket -- add it to 10-foundation's data_domains."
  }
}

# --- shared curation artefacts ---------------------------------------------------

resource "aws_s3_object" "glue_script" {
  bucket = local.f.artifacts_bucket.id
  key    = "glue/curate_domain.py"
  source = "${path.module}/../../../glue/curate_domain.py"
  etag   = filemd5("${path.module}/../../../glue/curate_domain.py")
}

resource "aws_s3_object" "lake_spec" {
  bucket       = local.f.artifacts_bucket.id
  key          = "glue/lake_tables.json"
  content      = file("${path.module}/lake_tables.json")
  content_type = "application/json"
}

resource "aws_s3_object" "dq_rulesets" {
  bucket       = local.f.artifacts_bucket.id
  key          = "glue/dq_rulesets.json"
  content      = jsonencode(merge([for d, m in module.domain : m.rulesets]...))
  content_type = "application/json"
}

# --- the producer-side role every curation job runs as -------------------------

data "aws_iam_policy_document" "glue_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_curation" {
  name               = "${local.name}-glue-curation"
  assume_role_policy = data.aws_iam_policy_document.glue_trust.json
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_curation.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_curation" {
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = flatten([for d in local.table_domains : [local.f.lake_buckets[d].arn, "${local.f.lake_buckets[d].arn}/raw/*"]])
  }
  statement {
    sid       = "WriteCurated"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:GetObject"]
    resources = [for d in local.table_domains : "${local.f.lake_buckets[d].arn}/curated/*"]
  }
  statement {
    sid       = "ReadArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${local.f.artifacts_bucket.arn}/glue/*"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:Encrypt"]
    resources = [local.f.kms_key_arn]
  }
  statement {
    sid       = "PublishDataQuality"
    actions   = ["glue:PublishDataQuality", "cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "glue_curation" {
  role   = aws_iam_role.glue_curation.id
  policy = data.aws_iam_policy_document.glue_curation.json
}

# --- one data product per contract domain -------------------------------------

module "domain" {
  source   = "../../modules/lake_domain"
  for_each = local.tables_by_dom

  name_prefix             = local.name
  domain                  = each.key
  tables                  = each.value
  lake_bucket             = local.f.lake_buckets[each.key]
  artifacts_bucket        = local.f.artifacts_bucket
  kms_key_arn             = local.f.kms_key_arn
  glue_job_role_arn       = aws_iam_role.glue_curation.arn
  lake_spec_uri           = "s3://${local.f.artifacts_bucket.id}/${aws_s3_object.lake_spec.key}"
  dq_rulesets_uri         = "s3://${local.f.artifacts_bucket.id}/glue/dq_rulesets.json"
  glue_script_uri         = "s3://${local.f.artifacts_bucket.id}/${aws_s3_object.glue_script.key}"
  lf_tag_domain_key       = local.f.lf_tags.domain
  lf_tag_data_product_key = local.f.lf_tags.data_product
}

# --- market_events: the exogenous event feed as a raw data product -------------
# Not in the FDM contract (it is not client data), so no curation job: the
# agents read the CSV object directly; this table makes it discoverable and
# queryable in SMUS/Athena.

resource "aws_glue_catalog_database" "events" {
  name         = replace("${local.name}_${local.events_domain}", "-", "_")
  description  = "Data product of the market_events domain: exogenous events (simulated public tender awards). Synthetic."
  location_uri = "s3://${local.f.lake_buckets[local.events_domain].id}/raw/"
}

resource "aws_lakeformation_resource_lf_tags" "events" {
  database {
    name = aws_glue_catalog_database.events.name
  }
  lf_tag {
    key   = local.f.lf_tags.domain
    value = local.events_domain
  }
  lf_tag {
    key   = local.f.lf_tags.data_product
    value = "raw"
  }
}

resource "aws_glue_catalog_table" "tender_events" {
  database_name = aws_glue_catalog_database.events.name
  name          = "tender_events"
  description   = "One row per exogenous event the exposure qualifier can match to clients (external_events/exposure_qualifier.py)."
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                 = "TRUE"
    classification           = "csv"
    "skip.header.line.count" = "1"
  }
  storage_descriptor {
    location      = "s3://${local.f.lake_buckets[local.events_domain].id}/raw/tender_events/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.OpenCSVSerde"
      parameters            = { separatorChar = ",", quoteChar = "\"", escapeChar = "\\" }
    }
    dynamic "columns" {
      # header of external_events/output_fdm/tender_events.csv; OpenCSVSerde reads strings
      for_each = ["event_id", "event_date", "event_type", "source_name", "real_source_type", "source_context",
      "affected_country", "affected_sector", "direction", "severity", "estimated_value_eur", "headline", "description"]
      content {
        name = columns.value
        type = "string"
      }
    }
  }
}

# --- Lake Formation: register the lake so LF vends access ----------------------

data "aws_iam_policy_document" "lf_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:SetContext", "sts:SetSourceIdentity"]
    principals {
      type        = "Service"
      identifiers = ["lakeformation.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lf_data_access" {
  name               = "${local.name}-lf-data-access"
  assume_role_policy = data.aws_iam_policy_document.lf_trust.json
}

data "aws_iam_policy_document" "lf_data_access" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = flatten([for d, b in local.f.lake_buckets : [b.arn, "${b.arn}/*"]])
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:Encrypt"]
    resources = [local.f.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "lf_data_access" {
  role   = aws_iam_role.lf_data_access.id
  policy = data.aws_iam_policy_document.lf_data_access.json
}

resource "aws_lakeformation_resource" "lake" {
  for_each = local.f.lake_buckets
  arn      = each.value.arn
  role_arn = aws_iam_role.lf_data_access.arn
}

# --- SageMaker Unified Studio --------------------------------------------------

module "smus" {
  source = "../../modules/smus"
  count  = var.enable_smus ? 1 : 0

  name_prefix             = local.name
  vpc_id                  = local.f.vpc_id
  private_subnet_ids      = local.f.private_subnet_ids
  azs                     = local.f.azs
  producer_domains        = merge({ for d, m in module.domain : d => m.database }, { (local.events_domain) = aws_glue_catalog_database.events.name })
  owner_arns              = var.smus_owner_arns
  identity_center_enabled = var.smus_identity_center_enabled
  glossary_terms          = local.glossary_terms
}
