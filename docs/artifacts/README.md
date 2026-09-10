# Exported artifacts

Standalone HTML files, each one self-contained (open directly in any
browser — no server needed). These are local exports of pages originally
published as Claude Artifacts during this project's build; the published
versions remain the canonical ones if you're still in the same
conversation and want to republish/update them.

| File | What it is |
|---|---|
| `pipeline-blueprint.html` | How the current pipeline actually runs — source → detect → rank → narrate → digest, with the evaluator isolation boundary |
| `production-blueprint.html` | The gap between this POC and a shippable production system, stage by stage |
| `simulation-results.html` | Real numbers from a live run — test results, category breakdown, sample worklist rows |
| `deployment-options.html` | Three deployment paths compared: MacBook, Snowflake+Glue/Iceberg, and why "Kiro-hosted" isn't an independent architecture |
| `first-to-notice-pitch.html` | The sponsor-facing pitch: business problem, inputs, RM-facing output, profit/trust benefits |
| `output-reference.html` | Raw CSV/markdown output snapshots and the six category-tag definitions, for a technical audience |
| `demo-run-sheet.html` | Step-by-step live-demo script: what to say and what to run, in order, covering architecture, statistical/rule/LLM logic, both event categories, and local-vs-Snowflake |

Generated 2026-09-05 (updated 2026-09-10) from a synthetic 300-client proof-of-concept dataset.
No real client, transaction, or event data is represented in any of them.
