# AWS deployment — HCP Terraform → AWS foundation → data mesh (SMUS) → AgentCore agents

Authorized 2026-10-23 by the project owner: provisioning in **your own AWS
account** through HCP Terraform, and **Amazon Bedrock for the AgentCore
deployment only**. Everything local (laptop, dashboard, CI, tests) stays
Ollama-only and hard-blocked from paid calls — see "The paid-inference
gate" below.

**Status: NOT RUN against AWS.** Every Terraform stack passes
`terraform validate` against the real provider schemas (aws 6.70,
awscc 1.107, tfe 0.82). The Python paths pass the test suite with fakes
and DuckDB standing in for AWS. Nothing has been planned against a real
account, applied, built, pushed or invoked. The first real plan/apply is
yours.

## What gets built

```
 HCP Terraform project "datainsights"  (VCS-driven, OIDC to AWS: read-only role for plan, admin role for apply)
 ├─ 00-bootstrap      (run once, locally)  OIDC provider + roles, workspaces, variable set, run triggers
 ├─ 10-foundation  →  VPC (3 private subnets, 1 NAT), KMS, one lake bucket per domain,
 │                    artifacts + Athena-results buckets, Athena workgroup (1 GB/query cap), ECR,
 │                    Lake Formation admins + LF-tags, GitHub OIDC push role, budget
 ├─ 20-data-mesh   →  per contract domain (kernel, lending): Glue DB (LF-tagged), contract-typed
 │      ▲ run          Parquet tables, Glue DQ rulesets, Glue 5.1 curation job
 │      │ trigger      market_events: Glue DB + CSV table over the event feed
 │                    LF data-access role + registered lake locations
 │                    SMUS V2 domain, Tooling + Lakehouse blueprints, project profile,
 │                    producer projects (kernel, lending, market_events), consumer project (nba-agents),
 │                    "NBA categories" glossary (from config/categories.yaml)
 └─ 30-agents      →  AgentCore Runtime (the repo's container), execution role, Bedrock guardrail,
                      DynamoDB run ledger, insights bucket, LF-tag SELECT grants for the agent
```

**One source of truth for the lake schema:** `config/entities_fdm.yaml` →
`python -m datainsights.deploy.lake_contract` → `infra/terraform/stacks/20-data-mesh/lake_tables.json`.
Terraform builds the tables and DQ rules from that JSON, the Glue job casts
to it, and `tests/test_aws_read_path.py` loads local data the same way and
asserts the **whole book gives identical recommendations** through the
lake-shaped SQL source as through local files. CI fails if the JSON is stale.

**How the agent reads data:** Athena over the curated Glue databases, one
per domain (`source.backend: glue_athena`, `domain_schema_map`). It has no
S3 permission on the lake. Lake Formation vends access through LF-tag
grants (`domain IN (kernel, lending)`).

**What the agent writes:** `s3://<insights bucket>/insights/<as_of>/<run_id>/` and
`.../insights/latest/`: `fdm_rm_worklist.csv`, `fdm_rm_digest.md`,
`fdm_insights.json`. It also writes one DynamoDB ledger item per run, with a
cross-session lock (`max_concurrent_runs: 1`).

## The paid-inference gate (two keys)

| Key | Where it lives | What it does |
|---|---|---|
| `config/profiles/fdm_agentcore.yaml`: `llm.provider: bedrock` + dated `cost.paid_authorization` | git (reviewed) | `datainsights/config.py` refuses Bedrock, or any paid flag, on any profile that isn't `runtime.target: agentcore` with that authorization |
| `DATAINSIGHTS_PAID_AWS_AUTHORIZED=1` | set only on the AgentCore Runtime by `modules/agent_runtime` | `agents/model_factory.get_model()` raises `PermissionError` without it, so a laptop loading the agentcore profile still can't call Bedrock |

Other limits: the runtime role can invoke **one** model (`bedrock_model_id`)
and nothing else. Every call goes through a Bedrock guardrail that
anonymises personal and account identifiers. Whole-book runs never narrate
(no LLM calls). Only single-client calls with `narrate: true` reach
Bedrock. Snowflake Cortex and the internal Model Gateway remain
unauthorized.

## Runbook

### 0. Prerequisites (once)

- AWS account and a console admin role. Note its ARN; this is `lake_admin_arns`.
- A region that offers AgentCore Runtime, SageMaker Unified Studio and your model. `us-east-1` is the default.
- **Bedrock model access:** in the Bedrock console, enable the model you'll use. Anthropic models need the one-time use-case form, which you submit yourself; Terraform doesn't. Get the ID: `aws bedrock list-inference-profiles --query "inferenceProfileSummaries[].inferenceProfileId"`.
- HCP Terraform: an organization, `terraform login`, and the **HCP Terraform GitHub App** installed on the repo's account (Org settings → Version control).
- Push this repo, including this branch, to GitHub. Workspaces track `main` by default (`vcs_branch`).
- Billing → Cost allocation tags: activate `project`, so the budget's tag filter has data.

### 1. Bootstrap (locally, once)

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars        # fill in; gitignored
export TF_CLOUD_ORGANIZATION=<your-hcp-org>
terraform init                                      # creates workspace datainsights-00-bootstrap
#  -> in HCP Terraform set that workspace's Execution Mode to "Local"
terraform apply                                     # uses YOUR AWS credentials, once
```

This creates the OIDC trust and two roles (`hcp-terraform-datainsights-plan` with ReadOnlyAccess,
`-apply` with AdministratorAccess). It also creates the three VCS-driven
workspaces with their inputs, state sharing and run triggers. After this,
no AWS keys exist anywhere in HCP Terraform.

### 2. Foundation

In HCP Terraform, open `datainsights-10-foundation` and start a new run. Review the plan, then confirm.

- **Read this first:** `aws_lakeformation_data_lake_settings` is account-wide. It replaces the Lake Formation admins and turns off IAM-only access for new databases. On a fresh account that is what you want; on a shared one, review it.

A successful apply queues a plan in `20-data-mesh` (run trigger).

### 3. Data mesh + SMUS

Confirm the `datainsights-20-data-mesh` plan. SMUS project creation deploys
each project's Tooling and Lakehouse environments in the background. That
takes several minutes after the apply finishes.

### 4. Load and curate the data (producer step)

```bash
python -m data_generator.fdm.generate_fdm --seed 42 && python -m data_generator.fdm.generate_fdm_events
pip install -r requirements-aws.txt
python -m datainsights.deploy.publish_raw --curate   # upload the allow-listed CSVs, run each domain's Glue job, wait
```

Only contracted tables and the event file are uploaded. Holdouts and
`protected_evaluator_only/` can't be. A cast failure or a failed DQ rule
fails that domain's job. Glue DQ results appear in the Glue console for
each table.

### 5. Build and deploy the agent

1. Set the GitHub Actions variables and secret listed at the top of `.github/workflows/agent-image.yml`. Their values are outputs of `10-foundation` and `bootstrap`.
2. Run the **agent-image** workflow (Actions → agent-image → Run workflow). It builds ARM64, checks the image contains no CSV, Parquet or protected paths, pushes `:<sha>`, sets `agent_image_tag` on `30-agents`, and queues a run.
3. Confirm the `datainsights-30-agents` plan. Until an image exists, this plan fails on purpose with "image not found".

### 6. Publish the data products in SMUS (the data-mesh loop)

```bash
python -m datainsights.deploy.smus_catalog
```

For each producer project, this grants the project role Lake Formation
access with grant option, creates a Glue data source that publishes on
import, and runs it. Then, in the SMUS portal (`20-data-mesh` output
`smus.portal_url`):

1. Project **nba-agents** → *Discover* → find the kernel and lending assets → *Subscribe*.
2. The producer project (**kernel**, **lending**) → *Subscription requests* → *Approve*. SMUS applies the Lake Formation grants. No manual IAM grant is involved.
3. In **nba-agents**, query the subscribed tables in the query editor or a notebook. This is the same data the AgentCore agent reads, so you can explore the structures it reasons over.

If the script's DataZone calls fail (the SMUS API is newer than the tested
SDK), do steps 1–3 in the portal. Under the producer project: *Data* →
*Add data source* → *AWS Glue* → the domain's database, with *Publish on
import* on.

### 7. Run the agents

```bash
python -m datainsights.deploy.invoke_agent --action book                 # whole book, no LLM -> S3 worklist + ledger
python -m datainsights.deploy.invoke_agent --prty-id PRTY00036           # one client, Bedrock-narrated (billed)
aws s3 cp s3://<insights bucket>/insights/latest/fdm_rm_digest.md -
```

Logs and traces are in CloudWatch under `/aws/bedrock-agentcore/runtimes/...`.
For the GenAI Observability dashboard, enable CloudWatch Transaction Search
once per account.

## Cost: what runs when idle

| Always on | Roughly |
|---|---|
| NAT gateway (`enable_nat_gateway`) | ~USD 33/month + data. Set `false` if you won't use SMUS spaces |
| KMS key | USD 1/month |
| SMUS domain / projects | no idle charge for the domain; notebook spaces bill only while running |

Pay-per-use: Glue (2 × G.1X workers per curation run, a few minutes),
Athena (USD 5/TB scanned; this dataset is MBs; a 1 GB per-query cap is
enforced), AgentCore Runtime (active vCPU/GB-seconds only), Bedrock
(tokens; only narrated single-client calls), DynamoDB on-demand, S3,
CloudWatch. The budget in `10-foundation` alerts at 80% forecast and 100%
actual if you set `budget_alert_emails`.

## Teardown

Destroy in reverse: `30-agents` → `20-data-mesh` → `10-foundation`, each
from Workspace → Settings → Destruction. Then destroy bootstrap locally.
Versioned buckets with objects block destroy by design; empty them first.
The SMUS domain deletes its projects' environments, which can take a while.

## Verified / assumed / not run

**Verified here, 2026-10-23:**
- `terraform validate` passes for bootstrap and all three stacks, against the downloaded provider schemas. Every resource and argument used exists in those versions.
- `pytest tests/ -k "not live"` passes. That includes the whole-book parity test through the lake-shaped source, the paid-gate tests, the S3/DynamoDB backends with fakes, the upload allow-list, the Terraform env-var coverage check, and the lake-spec staleness check.
- pyathena 4.2.1's real parameter formatter renders this repo's queries (named params, `DATE '…'` literals).
- `requirements.txt` + `requirements-aws.txt` resolve together.

**Assumed, verify on first plan/apply:**
- The SMUS V2 blueprint names `Tooling` and `DataLake` and their regional parameter keys (`S3Location`, `VpcId`, `Subnets`, `AZs`). A wrong blueprint name fails at **plan**.
- The managed policy names the SMUS roles attach (`SageMakerStudioDomainExecutionRolePolicy`, …). A wrong name also fails at plan, because they're data-source lookups.
- Glue version `5.1` being accepted in your region.
- The DataZone API shapes in `smus_catalog.py`. The portal fallback above does the same thing.
- Region support for AgentCore, SMUS and your model in the same region.

**Not run:** anything against AWS or HCP Terraform; the container build
(no Docker run here); the Glue job; Athena; Bedrock; AgentCore.

**Known limits:**
- Whole-book runs on AWS are always full, not incremental. The watermark lives in a per-session sqlite file (`datainsights/aws_backends.py` explains why).
- The apply role is AdministratorAccess. Add a permissions boundary before this is more than a POC.
- The agent is a service consumer granted by LF-tag in Terraform, not through an SMUS subscription. SMUS subscriptions are the human and consumer-project path.
