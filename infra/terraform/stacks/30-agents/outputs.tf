output "agent_runtime_arn" {
  value = module.agent.runtime_arn
}

output "insights_bucket" {
  value = module.agent.insights_bucket
}

output "ledger_table" {
  value = module.agent.ledger_table
}

output "guardrail" {
  value = module.agent.guardrail
}

output "invoke_book_run" {
  value = "python -m datainsights.deploy.invoke_agent --action book"
}
