output "glue_databases" {
  value = merge({ for d, m in module.domain : d => m.database }, { (local.events_domain) = aws_glue_catalog_database.events.name })
}

output "glue_jobs" {
  value = { for d, m in module.domain : d => m.job_name }
}

output "events_s3_uri" {
  value = "s3://${local.f.lake_buckets[local.events_domain].id}/raw/tender_events/tender_events.csv"
}

output "lf_tag_domain_key" {
  value = local.f.lf_tags.domain
}

output "table_domains" {
  value = sort(tolist(local.table_domains))
}

output "smus" {
  value = var.enable_smus ? {
    domain_id   = module.smus[0].domain_id
    portal_url  = module.smus[0].portal_url
    project_ids = module.smus[0].project_ids
  } : null
}

resource "aws_ssm_parameter" "discovery" {
  for_each = merge(
    { for d, m in module.domain : "mesh/${d}/glue_job" => m.job_name },
    { for d, db in merge({ for d, m in module.domain : d => m.database }, { (local.events_domain) = aws_glue_catalog_database.events.name }) : "mesh/${d}/glue_database" => db },
    var.enable_smus ? merge(
      { "smus/domain_id" = module.smus[0].domain_id },
      { for p, id in module.smus[0].project_ids : "smus/project/${p}" => id },
    ) : {},
  )
  name  = "/${var.project}/${var.environment}/${each.key}"
  type  = "String"
  value = each.value
}
