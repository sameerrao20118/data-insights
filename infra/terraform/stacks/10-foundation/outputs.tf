# Read by stacks/20-data-mesh and stacks/30-agents (terraform_remote_state)
# and, through SSM, by the operator scripts in datainsights/deploy/.

output "name_prefix" {
  value = local.name
}

output "account_id" {
  value = local.account_id
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "azs" {
  value = module.network.azs
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}

output "lake_buckets" {
  value = { for d, b in module.lake : d => { id = b.id, arn = b.arn } }
}

output "artifacts_bucket" {
  value = { id = module.artifacts.id, arn = module.artifacts.arn }
}

output "athena_results_bucket" {
  value = { id = module.athena_results.id, arn = module.athena_results.arn }
}

output "athena_workgroup" {
  value = { name = aws_athena_workgroup.agents.name, arn = aws_athena_workgroup.agents.arn }
}

output "ecr_repository" {
  value = { url = aws_ecr_repository.agent.repository_url, arn = aws_ecr_repository.agent.arn, name = aws_ecr_repository.agent.name }
}

output "lf_tags" {
  value = { domain = aws_lakeformation_lf_tag.domain.key, data_product = aws_lakeformation_lf_tag.data_product.key }
}

output "data_domains" {
  value = var.data_domains
}

output "github_image_push_role_arn" {
  value = local.github_enabled ? aws_iam_role.github_image_push[0].arn : null
}

resource "aws_ssm_parameter" "discovery" {
  for_each = merge(
    { for d, b in module.lake : "lake/${d}/bucket" => b.id },
    {
      "artifacts/bucket" = module.artifacts.id
      "athena/workgroup" = aws_athena_workgroup.agents.name
      "ecr/repository"   = aws_ecr_repository.agent.repository_url
    },
  )
  name  = "/${var.project}/${var.environment}/${each.key}"
  type  = "String"
  value = each.value
}
