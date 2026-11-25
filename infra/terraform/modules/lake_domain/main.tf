# One data-mesh domain as a self-contained data product:
#   Glue database (LF-tagged domain=<d>) -> contract-typed Parquet tables
#   -> Glue Data Quality rulesets -> a Glue 5.1 curation job.
# Everything is derived from lake_tables.json, i.e. from the source
# contract (config/entities_fdm.yaml) -- add an entity there, regenerate,
# and the table, its DQ rules and its curation appear here.
#
# Data-mesh guardrails (skill: data-mesh-pipeline-builder):
#   domain ownership        every resource tagged domain/data-product
#   data-as-a-product       every table has a description before publication
#   federated governance    the DQ ruleset belongs to the producer domain

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.70" }
  }
}

variable "name_prefix" {
  type = string
}

variable "domain" {
  type = string
}

variable "tables" {
  description = "lake_tables.json `tables` entries for this domain."
  type = list(object({
    entity           = string
    table            = string
    domain           = string
    primary_key      = list(string)
    event_time_field = optional(string)
    columns = list(object({
      name        = string
      source_name = string
      type        = string
      nullable    = bool
    }))
  }))
}

variable "lake_bucket" {
  type = object({ id = string, arn = string })
}

variable "artifacts_bucket" {
  type = object({ id = string, arn = string })
}

variable "kms_key_arn" {
  type = string
}

variable "glue_job_role_arn" {
  type = string
}

variable "lake_spec_uri" {
  type = string
}

variable "dq_rulesets_uri" {
  type = string
}

variable "glue_script_uri" {
  type = string
}

variable "lf_tag_domain_key" {
  type = string
}

variable "lf_tag_data_product_key" {
  type = string
}

locals {
  database = replace("${var.name_prefix}_${var.domain}", "-", "_")
  tables   = { for t in var.tables : t.table => t }
  tags     = { domain = var.domain, data-product = "${var.domain}-curated" }
}

resource "aws_glue_catalog_database" "this" {
  name         = local.database
  description  = "Data product of the ${var.domain} domain: curated, contract-typed FDM tables (config/entities_fdm.yaml). Synthetic data only."
  location_uri = "s3://${var.lake_bucket.id}/curated/"
  tags         = local.tags
}

resource "aws_lakeformation_resource_lf_tags" "database" {
  database {
    name = aws_glue_catalog_database.this.name
  }
  lf_tag {
    key   = var.lf_tag_domain_key
    value = var.domain
  }
  lf_tag {
    key   = var.lf_tag_data_product_key
    value = "curated"
  }
}

resource "aws_glue_catalog_table" "this" {
  for_each      = local.tables
  database_name = aws_glue_catalog_database.this.name
  name          = each.key
  description   = "FDM ${each.value.entity} (${var.domain} domain). Primary key: ${join(", ", each.value.primary_key)}.${each.value.event_time_field != null ? " Event time: ${each.value.event_time_field}." : ""} Curated by ${var.name_prefix}-${var.domain}-curate from raw/${each.key}/."
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL       = "TRUE"
    classification = "parquet"
    domain         = var.domain
    contract       = "config/entities_fdm.yaml#${each.value.entity}"
  }

  storage_descriptor {
    location      = "s3://${var.lake_bucket.id}/curated/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }
    dynamic "columns" {
      for_each = each.value.columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = "${columns.value.source_name}${columns.value.nullable ? "" : " (required)"}"
      }
    }
  }
}

# DQDL derived from the contract: the table is non-empty, every non-nullable
# column is complete, the primary key is unique and complete.
# ref: docs.aws.amazon.com/glue/latest/dg/dqdl.html
locals {
  rulesets = {
    for name, t in local.tables : name => format("Rules = [ %s ]", join(", ", concat(
      ["RowCount > 0"],
      [for c in t.columns : format("IsComplete \"%s\"", c.name) if !c.nullable],
      length(t.primary_key) > 0 ? [format("IsPrimaryKey %s", join(" ", [for k in t.primary_key : format("\"%s\"", k)]))] : [],
    )))
  }
}

resource "aws_glue_data_quality_ruleset" "this" {
  for_each    = local.tables
  name        = "${local.database}_${each.key}"
  description = "Contract rules for ${var.domain}.${each.key} -- owned by the ${var.domain} producer domain."
  ruleset     = local.rulesets[each.key]
  target_table {
    database_name = aws_glue_catalog_database.this.name
    table_name    = aws_glue_catalog_table.this[each.key].name
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "job" {
  name              = "/aws-glue/jobs/${var.name_prefix}-${var.domain}-curate"
  retention_in_days = 30
}

resource "aws_glue_job" "curate" {
  name              = "${var.name_prefix}-${var.domain}-curate"
  description       = "raw CSV -> contract-typed, DQ-checked Parquet for the ${var.domain} domain"
  role_arn          = var.glue_job_role_arn
  glue_version      = "5.1"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30
  max_retries       = 0

  execution_property {
    max_concurrent_runs = 1
  }

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = var.glue_script_uri
  }

  default_arguments = {
    "--domain"                           = var.domain
    "--lake_bucket"                      = var.lake_bucket.id
    "--lake_spec_uri"                    = var.lake_spec_uri
    "--dq_rulesets_uri"                  = var.dq_rulesets_uri
    "--job-bookmark-option"              = "job-bookmark-disable"
    "--enable-metrics"                   = "true"
    "--enable-observability-metrics"     = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--continuous-log-logGroup"          = aws_cloudwatch_log_group.job.name
  }

  tags = local.tags
}

output "database" {
  value = aws_glue_catalog_database.this.name
}

output "tables" {
  value = keys(local.tables)
}

output "job_name" {
  value = aws_glue_job.curate.name
}

output "rulesets" {
  value = local.rulesets
}
