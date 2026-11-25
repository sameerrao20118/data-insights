# The NBA agent on Amazon Bedrock AgentCore Runtime -- the container built
# from the repo's Dockerfile, running agents/entrypoint.py with
# DATAINSIGHTS_PROFILE=fdm_agentcore.
#
# What it can touch, and nothing else:
#   read   the curated lake, ONLY through Lake Formation LF-tag grants
#          (domain in var.read_domains) via Athena, plus the event feed object
#   write  its own insights bucket (worklist/digest/insights JSON) and its
#          DynamoDB run ledger
#   call   ONE Bedrock model (inference profile + the foundation model behind
#          it), through ONE guardrail
#
# Paid-inference gate: DATAINSIGHTS_PAID_AWS_AUTHORIZED=1 is set here and
# nowhere else (agents/model_factory.py). NOT RUN until applied.
# ref: docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.70" }
  }
}

variable "name_prefix" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "ecr_repository_arn" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "athena_workgroup" {
  type = object({ name = string, arn = string })
}

variable "athena_results_bucket" {
  type = object({ id = string, arn = string })
}

variable "glue_databases" {
  type        = map(string)
  description = "domain -> Glue database (all of them; read_domains selects)."
}

variable "read_domains" {
  type = list(string)
}

variable "lf_tag_domain_key" {
  type = string
}

variable "lake_bucket_arns" {
  type        = list(string)
  description = "for KMS/S3 metadata only -- table data access is vended by Lake Formation."
}

variable "events_s3_uri" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.region
  runtime_name = replace("${var.name_prefix}_nba_agent", "-", "_") # AgentCore: [a-zA-Z][a-zA-Z0-9_]{0,47}

  # A cross-region inference profile id ("us.", "eu.", "global." ...) needs
  # the profile ARN AND the foundation model ARN in every region it routes to.
  is_inference_profile = can(regex("^(us|eu|apac|jp|au|ca|us-gov|global)\\.", var.bedrock_model_id))
  base_model_id        = local.is_inference_profile ? join(".", slice(split(".", var.bedrock_model_id), 1, length(split(".", var.bedrock_model_id)))) : var.bedrock_model_id
  model_arns = local.is_inference_profile ? [
    "arn:${data.aws_partition.current.partition}:bedrock:${local.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
    "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${local.base_model_id}",
    ] : [
    "arn:${data.aws_partition.current.partition}:bedrock:${local.region}::foundation-model/${var.bedrock_model_id}",
  ]

  events_bucket = split("/", trimprefix(var.events_s3_uri, "s3://"))[0]
  events_key    = join("/", slice(split("/", trimprefix(var.events_s3_uri, "s3://")), 1, length(split("/", trimprefix(var.events_s3_uri, "s3://")))))
}

# --- outputs of the agent: its own data product + run ledger -----------------

module "insights" {
  source      = "../secure_bucket"
  name        = "${var.name_prefix}-insights-${local.account_id}"
  kms_key_arn = var.kms_key_arn
  tags        = { domain = "nba_agents", data-product = "nba-worklist" }
}

resource "aws_dynamodb_table" "ledger" {
  name         = "${var.name_prefix}-agent-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
  # the run lock item carries expires_at; DynamoDB reaps stale ones too
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# --- Bedrock guardrail: client identifiers never leave as plain text ----------

resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.name_prefix}-nba-narration"
  description               = "Masks personal and account identifiers in what the NBA agent sends to and receives from the model."
  blocked_input_messaging   = "This request was blocked by the DataInsights guardrail."
  blocked_outputs_messaging = "This response was blocked by the DataInsights guardrail."
  kms_key_arn               = var.kms_key_arn

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["NAME", "EMAIL", "PHONE", "ADDRESS", "UK_NATIONAL_INSURANCE_NUMBER", "UK_UNIQUE_TAXPAYER_REFERENCE_NUMBER",
      "INTERNATIONAL_BANK_ACCOUNT_NUMBER", "SWIFT_CODE", "UK_NATIONAL_HEALTH_SERVICE_NUMBER", "CREDIT_DEBIT_CARD_NUMBER"]
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "MISCONDUCT", "SEXUAL", "VIOLENCE"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "pinned by terraform"
}

# --- the runtime's execution role ---------------------------------------------

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.name_prefix}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [var.ecr_repository_arn]
  }
  statement {
    sid       = "EcrToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
      "arn:${data.aws_partition.current.partition}:logs:${local.region}:${local.account_id}:log-group:*",
    ]
  }
  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }
  statement {
    sid     = "WorkloadIdentity"
    actions = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
    ]
  }
  statement {
    sid       = "InvokeTheOneModel"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
    resources = local.model_arns
  }
  statement {
    sid       = "Guardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.this.guardrail_arn]
  }
  statement {
    sid       = "Athena"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"]
    resources = [var.athena_workgroup.arn]
  }
  statement {
    sid     = "CatalogMetadata"
    actions = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = concat(
      ["arn:${data.aws_partition.current.partition}:glue:${local.region}:${local.account_id}:catalog"],
      flatten([for d in var.read_domains : [
        "arn:${data.aws_partition.current.partition}:glue:${local.region}:${local.account_id}:database/${var.glue_databases[d]}",
        "arn:${data.aws_partition.current.partition}:glue:${local.region}:${local.account_id}:table/${var.glue_databases[d]}/*",
      ]]),
    )
  }
  statement {
    sid       = "LakeFormationVendsTheData"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }
  statement {
    sid       = "AthenaResults"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation", "s3:AbortMultipartUpload"]
    resources = [var.athena_results_bucket.arn, "${var.athena_results_bucket.arn}/agents/*"]
  }
  statement {
    sid       = "EventFeed"
    actions   = ["s3:GetObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${local.events_bucket}/${local.events_key}"]
  }
  statement {
    sid       = "WriteInsights"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [module.insights.arn, "${module.insights.arn}/*"]
  }
  statement {
    sid       = "Ledger"
    actions   = ["dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.ledger.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:Encrypt", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "runtime" {
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

# --- Lake Formation: the agent reads by LF-tag, never by path -----------------

resource "aws_lakeformation_permissions" "databases" {
  principal   = aws_iam_role.runtime.arn
  permissions = ["DESCRIBE"]
  lf_tag_policy {
    resource_type = "DATABASE"
    expression {
      key    = var.lf_tag_domain_key
      values = var.read_domains
    }
  }
}

resource "aws_lakeformation_permissions" "tables" {
  principal   = aws_iam_role.runtime.arn
  permissions = ["SELECT", "DESCRIBE"]
  lf_tag_policy {
    resource_type = "TABLE"
    expression {
      key    = var.lf_tag_domain_key
      values = var.read_domains
    }
  }
}

# --- the runtime ------------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = local.runtime_name
  description        = "DataInsights NBA agents: detectors over the data-mesh lake, Bedrock narration, RM worklist to S3."
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.image_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  environment_variables = {
    DATAINSIGHTS_PROFILE                   = "fdm_agentcore"
    DATAINSIGHTS_AGENTCORE_SERVE           = "1"
    DATAINSIGHTS_PAID_AWS_AUTHORIZED       = "1"
    DATAINSIGHTS_BEDROCK_MODEL_ID          = var.bedrock_model_id
    DATAINSIGHTS_BEDROCK_GUARDRAIL_ID      = aws_bedrock_guardrail.this.guardrail_id
    DATAINSIGHTS_BEDROCK_GUARDRAIL_VERSION = aws_bedrock_guardrail_version.this.version
    DATAINSIGHTS_GLUE_DB_KERNEL            = var.glue_databases["kernel"]
    DATAINSIGHTS_GLUE_DB_LENDING           = var.glue_databases["lending"]
    DATAINSIGHTS_ATHENA_WORKGROUP          = var.athena_workgroup.name
    ATHENA_REGION                          = local.region
    ATHENA_S3_STAGING_DIR                  = "s3://${var.athena_results_bucket.id}/agents/"
    DATAINSIGHTS_EVENTS_S3_URI             = var.events_s3_uri
    DATAINSIGHTS_LEDGER_TABLE              = aws_dynamodb_table.ledger.name
    DATAINSIGHTS_INSIGHTS_S3_URI           = "s3://${module.insights.id}/insights"
    OLLAMA_NO_CLOUD                        = "1"
    AGENT_OBSERVABILITY_ENABLED            = "true"
    OTEL_PYTHON_DISTRO                     = "aws_distro"
    OTEL_PYTHON_CONFIGURATOR               = "aws_configurator"
  }

  depends_on = [
    aws_iam_role_policy.runtime,
    aws_lakeformation_permissions.tables,
  ]
}

output "runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}

output "runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}

output "insights_bucket" {
  value = module.insights.id
}

output "ledger_table" {
  value = aws_dynamodb_table.ledger.name
}

output "guardrail" {
  value = { id = aws_bedrock_guardrail.this.guardrail_id, version = aws_bedrock_guardrail_version.this.version }
}
