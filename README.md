# DataInsights — Next Best Action for Commercial & Institutional Banking

A proof-of-concept pipeline that detects meaningful client events — a
client's own activity (deposit buildup, facility utilization, a credit-
risk grade change) and external events near them (a public tender award,
matched against that client's own confirming activity, not just a
sector/country broadcast) — and turns them into ranked, sized,
hypothesis-backed recommendations for a Relationship Manager. Built
against NatWest's Federated Data Model (`docs/fdm_reference.md`) and a
captured engineering Decision Record (`docs/decision_record.md`).

No real client data is used anywhere in this repo — every dataset is
synthetic, generated locally. No paid or cloud LLM calls — local Ollama
only, enforced in code, not just documented. See `CLAUDE.md` for the
full set of hard boundaries this project holds itself to.

## Start here

| I want to... | Go to |
|---|---|
| **Run it right now** | [Run it yourself](#run-it-yourself) below, or `docs/current_state.md`'s copy (kept in sync) |
| **Understand the architecture** | [`docs/architecture.md`](docs/architecture.md) — the pipeline diagram, component map, design decisions |
| **See what's verified vs. NOT RUN** | [`docs/current_state.md`](docs/current_state.md) — updated every session, the source of truth |
| **See where LLM agents fit and why** | [`docs/agentic_plan.md`](docs/agentic_plan.md) — which agent does what, what it can never decide, real measured results |
| **See the plan to make this generic** (any data model, any event type, local ↔ AWS) | [`docs/generalization_plan.md`](docs/generalization_plan.md) — phases, acceptance tests, current build status |
| **Add a new domain or detector** | [`docs/adding_a_new_domain.md`](docs/adding_a_new_domain.md) — the actual checklist, proven twice |
| **Hand this repo to another Claude session that can't see the code** | [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) — single-file condensed context |
| **See the captured source material this is built against** | [`docs/fdm_reference.md`](docs/fdm_reference.md) (the data model) and [`docs/decision_record.md`](docs/decision_record.md) (the engineering decisions) |
| **Check current gaps against that decision record** | [`docs/gap_analysis.md`](docs/gap_analysis.md) |
| **Understand the agent layer's files and AgentCore staging** | [`agents/README.md`](agents/README.md) |

## What this actually does, today

1. **Detects** endogenous signals (Deposits, Lending, Risk domains — see
   `docs/architecture.md`) from synthetic FDM-shaped data, and exogenous
   signals (a public tender award today; the event-type registry
   generalizing this is in progress — `docs/generalization_plan.md`
   Phase 2) qualified against a client's own confirming activity, never
   a broadcast.
2. **Assembles** whatever fired for a client into ONE ranked,
   category-tagged, sized `Recommendation` (`datainsights/correlation/`)
   — never one recommendation per detection.
3. **Narrates** it with local Ollama, validated against the evidence
   before acceptance, with a deterministic template fallback on any
   failure — the LLM never decides the category, score, or sizing.
4. **Surfaces** it to an RM: a worklist CSV, a markdown digest, and a
   Streamlit dashboard with an "ask about this recommendation" copilot
   and RM-response capture (Customer Engaged / Not Appropriate / Remind
   Me Later) — live, not a mockup.
5. **Ships schema-only** toward the real integration points (MIMO
   insight record, a Pega event mock) — never a real publish. See
   `CLAUDE.md`'s hard boundaries.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Local Ollama must be running with `qwen2.5:7b` pulled for anything that
narrates (`ollama pull qwen2.5:7b`); everything else (whole-book batch
runs, tests) works without it.

## Run it yourself

```bash
# 1. Generate the synthetic FDM dataset (dev + holdout)
python -m data_generator.fdm.generate_fdm --seed 42
python -m data_generator.fdm.generate_fdm --seed 1337
python -m data_generator.fdm.generate_fdm_events

# 2. Run the pipeline
python -m agents.demo_fdm_scenario           # whole book, no LLM, ~9s (60 clients)
python -m agents.demo_multiagent_scenario    # one client, live local Ollama narration, ~17s
streamlit run dashboard/app.py               # interactive dashboard, incl. the RM copilot + feedback panel
python -m datainsights.semantic.demo_canonical_read  # Phase 1 foundation: SAME data, canonical field names --
                                              #   see it working, and exactly what still uses physical names

# 3. Tests
python -m pytest tests/ -q -k "not live"     # deterministic-only, no Ollama required (~315 tests)
python -m pytest tests/ -v                   # everything, including live-Ollama-gated tests
```

Both demo scripts and the entrypoint (`agents/entrypoint.py`) now read a
**profile** (`config/profiles/fdm_local.yaml` by default —
`docs/generalization_plan.md` Phase 0) rather than a hard-coded path;
pass `--profile <name>` to point at a different one, e.g. the 300-party/
4-year scaled dataset. Full command reference, including A1 event
ingestion and the SLOT E2 ML comparison scripts, is kept current in
[`docs/current_state.md`](docs/current_state.md)'s "Run it yourself"
section — that copy is the one to trust if this one ever drifts.

## Repository map

```
config/                    entities_fdm.yaml (source contract), domains_fdm.yaml (domain registry),
                            semantic_model.yaml + bindings/ (canonical model, generalization_plan.md Phase 1),
                            profiles/ (runtime composition), rules.yaml (detector + baseline params)
data_generator/fdm/         synthetic FDM dataset generator (--n-parties/--history-years to scale)
datainsights/sources/       DataSource implementations (FdmLocalSource active; Snowflake/S3/Glue contract-only)
datainsights/semantic/      CanonicalSource -- schema-agnostic reads through a binding (Phase 1 foundation)
datainsights/correlation/   Signal Bus, Hypothesis Assembler, De-dup -- the cross-domain recommendation engine
datainsights/sinks/         RM worklist/digest, MIMO JSON, Pega event mock -- all render one Recommendation
datainsights/ml/            SLOT E2 baselines + whole-book/scaled comparison scripts
datainsights/runtime.py     build_runtime() -- the single composition point (Phase 0)
detection_engine/           deterministic detectors, one file each, same Config/detect()/to_signal() shape
agents/                     domain registry, narrating agent, A1 extraction, A2 investigator, A3 RM copilot
external_events/            exposure qualification (deterministic) + event extraction (A1, LLM+validated)
dashboard/app.py            Streamlit demo dashboard
tests/                      one file per module/concern -- see docs/current_state.md for the current count
docs/                       see the table above
```

## The legacy pipeline (kept, not extended)

A smaller, earlier pipeline (`datainsights/cli.py`,
`detection_engine/large_incoming_payment.py`,
`detection_engine/external_macro_event.py`, `evaluation/evaluate.py`)
predates the FDM build above and still passes its own tests
(`python -m datainsights.cli`, `python -m pytest tests/test_large_incoming_payment.py
tests/test_external_macro_event.py`). It's kept because this project's
own working convention is "continue from existing work, don't rebuild,"
and it is now load-bearing evidence for `docs/generalization_plan.md`'s
generality claim (it's the second binding proving a new schema doesn't
require touching detector code) — but it is not where new work happens.
See `docs/architecture.md`'s appendix if you need its detail.

## Hard boundaries (see `CLAUDE.md` for the full list)

- Local Ollama only. No paid or cloud LLM calls, enforced in code.
- Ground truth (`protected_evaluator_only/`) is never read by detection
  or agent code — three independent enforcement layers.
- No real production deployment or external communication — no emails,
  no CRM writes, no real MIMO/Pega/S3 publish, no AWS provisioning.
  AgentCore/Bedrock adapters are contract-and-mock only until explicitly
  authorized.
- Detector → correlation → sink roles stay separate; no agent decides a
  category, score, or offer size — it proposes, code validates, a
  person confirms.
