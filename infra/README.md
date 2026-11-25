# infra/

Terraform for the AWS deployment, deployed through HCP Terraform
(VCS-driven workspaces, keyless OIDC to AWS). **Runbook, costs, and what is
verified vs not run: [docs/aws_deployment.md](../docs/aws_deployment.md).**

```
terraform/
  bootstrap/            run once locally: HCP project + workspaces, AWS OIDC roles (plan=read-only, apply=admin)
  stacks/10-foundation  VPC, KMS, lake buckets per domain, Athena, ECR, Lake Formation, budget
  stacks/20-data-mesh   Glue DBs/tables/DQ/jobs per domain (from lake_tables.json), SMUS domain + projects
  stacks/30-agents      AgentCore Runtime, Bedrock guardrail, run ledger, insights bucket
  modules/              secure_bucket, network, lake_domain, smus, agent_runtime
glue/curate_domain.py   the Glue 5.1 curation job (raw CSV -> contract-typed, DQ-checked Parquet)
```

`stacks/20-data-mesh/lake_tables.json` is generated, never hand-edited:
`python -m datainsights.deploy.lake_contract` (CI fails if it's stale).
