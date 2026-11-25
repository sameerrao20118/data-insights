output "workspaces" {
  value = { for k, w in tfe_workspace.stack : k => "https://app.terraform.io/app/${var.tfc_organization}/workspaces/${w.name}" }
}

output "agents_workspace_id" {
  value       = tfe_workspace.stack["30-agents"].id
  description = "GitHub repo variable TFC_AGENTS_WORKSPACE_ID for the image workflow."
}

output "hcp_role_arns" {
  value = { for k, r in aws_iam_role.hcp : k => r.arn }
}
