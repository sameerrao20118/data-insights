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
| **See what a relationship manager actually gets** (the product, not the plumbing) | [`docs/user_guide.md`](docs/user_guide.md) — set it up, then read a recommendation the way an RM would |
| **Find which fields are worth ML, and run a champion/challenger** | [`docs/ml_quickstart.md`](docs/ml_quickstart.md) — baby steps, dashboard + CLI |
| **Onboard a new schema / data asset** | [`onboarding/README.md`](onboarding/README.md) — the single page for profile → propose → human-accept |
| **Understand how generic this really is, and the plan to make it so** | [`docs/hardcoding_audit.md`](docs/hardcoding_audit.md) (the evidence, file:line) → [`docs/refactor_plan.md`](docs/refactor_plan.md) (12 tasks in 4 waves, the trigger model, the RM journey and where learning enters) |
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
   signals (a public tender award and an FX-rate move today, both
   registered declaratively in `config/event_types.yaml` —
   `docs/generalization_plan.md` Phase 2) qualified against a client's
   own confirming activity, never a broadcast.
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

Local Ollama must be running with the model named in your profile's
`llm.model` pulled (`config/profiles/fdm_local.yaml` ships `qwen2.5:7b`:
`ollama pull qwen2.5:7b`) for anything that narrates; to change the
model follow `docs/model_change_procedure.md`. Everything else (whole-book batch
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
python -m datainsights.semantic.demo_canonical_read  # SAME data, canonical field names -- see the semantic layer working
python -m pytest tests/test_legacy_binding_end_to_end.py -q  # proof: the SAME detectors, zero edits, run against a
                                              #   second schema (config/bindings/legacy.yaml)
python -m data_generator.external.fetch_sba && python -m data_generator.fdm.load_sba  # once: real SBA PPP loan data
python -m pytest tests/test_sba_binding_end_to_end.py -q     # proof: a THIRD schema with REAL commercial entities/
                                              #   sectors (config/bindings/sba.yaml) -- 24 real NAICS sectors
python -m pytest tests/test_fx_exposure_end_to_end.py -q     # proof: a SECOND event type (fx_rate_move), registered
                                              #   in config/event_types.yaml only, qualifies for real

# 3. Onboard a new schema yourself, no hand-written binding
python -m onboarding.propose <data_dir> --name <schema_name>  # profile a CSV directory + LLM-propose a binding
cat onboarding/proposals/<schema_name>/review.md              # confidence/evidence/rejections -- read before accepting
python -m onboarding.accept <schema_name>                     # human-gated: validates, only then writes config/
python -m pytest tests/test_onboarding_live.py -v             # proof: blind-tested live against real SBA data (~3 min)

# 4. Self-service ML -- scan a schema for ML-eligible measures, set policy, run a comparison
#    Baby-steps guide (dashboard + CLI, every snippet verified to run): docs/ml_quickstart.md
python -m datainsights.ml.runner --profile fdm_local           # champion/challenger comparison -> var/ml_runs/

# 5. Listen: incremental runs on a clock, and the outcome backtest
python -m datainsights.runs --profile fdm_local            # recorded, locked, incremental whole-book run
python -m datainsights.monitor --profile fdm_local --once  # one clock tick (monitor: block in the profile)
python -m datainsights.backtest --profile fdm_local --start 2025-07-15 --end 2025-10-04 --step-days 30

# 6. Tests
python -m pytest tests/ -q -k "not live"     # deterministic-only, no Ollama required (~700 tests, ~1 min)
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

**Local Ollama and AgentCore are the same code, two profiles — not two
implementations.** Every entry point calls `agents/entrypoint.py`'s
`invoke(payload)`; the AgentCore-decorated handler
(`agentcore_entrypoint`) is that exact same function, registered
unchanged (`tests/test_entrypoint.py` proves both produce identical
output for the same payload — no server started, no network call).
`config/profiles/fdm_local.yaml` selects backends that run for real
today; `config/profiles/fdm_agentcore.yaml` selects the AgentCore-shaped
backends (S3/Glue source, DynamoDB state, S3 output, a model gateway) —
running `--profile fdm_agentcore` validates and constructs as far as it
honestly can, then raises a plain `NotImplementedError` naming the exact
first blocking reason (no real bucket/credentials, no adapter built
yet) rather than silently falling back to local files. That failure is
the correct, disclosed behavior — actually deploying to AgentCore is
Phase 3, contract-and-mock only until explicitly authorized (`CLAUDE.md`).

## Repository map

```
config/                    entities_fdm.yaml/entities_sba.yaml (source contracts), domains_fdm.yaml
                            (domain registry), semantic_model.yaml + bindings/ (canonical model +
                            fdm/legacy/sba bindings, generalization_plan.md Phase 1, DONE --
                            sba.yaml maps REAL SBA commercial loan data, not synthetic),
                            event_types.yaml (declarative exogenous event-type registry, Phase 2,
                            DONE), profiles/ (runtime composition), rules.yaml (detector params)
data_generator/fdm/         synthetic FDM dataset generator (--n-parties/--history-years to scale);
                            load_sba.py layers synthetic deposits onto real SBA loan entities
data_generator/external/    fetch_berka.py (retail, ruled out), fetch_sba.py (real US commercial
                            loan data, gitignored, rebuild locally)
datainsights/sources/       DataSource/EventSource implementations (FdmLocalSource, OfflineLocalSource,
                            CsvEventSource active; Snowflake/S3/Glue contract-only)
datainsights/semantic/      CanonicalSource -- every detector-facing tool/qualifier/worklist call site reads
                            through this now; swapping config/bindings/<name>.yaml is how a new schema plugs in
datainsights/correlation/   Signal Bus, Hypothesis Assembler, De-dup -- the cross-domain recommendation engine
datainsights/sinks/         RM worklist/digest, MIMO JSON, Pega event mock -- all render one Recommendation
datainsights/ml/            SLOT E2 baselines + whole-book/scaled comparison scripts
datainsights/runtime.py     build_runtime() -- the single composition point (Phase 0)
detection_engine/           deterministic detectors, one file each, same Config/detect()/to_signal() shape
agents/                     domain registry, narrating agent, A1 extraction, A2 investigator, A3 RM copilot
external_events/            event_registry.py + exposure_checks.py (declarative qualification, Phase 2) +
                            event extraction (A1, LLM+validated, dynamic per event type -- Phase 2, DONE)
dashboard/app.py            Streamlit demo dashboard
tests/                      one file per module/concern -- see docs/current_state.md for the current count
docs/                       see the table above
```

## One pipeline (the legacy pipeline was retired in R23)

The repo's original CLI pipeline (`datainsights/cli.py`, its own
detector, ranking, worklist, state and digest modules, and the
`external_macro_event` detector) is gone. Its one distinct detector,
`large_incoming_payment`, was ported into the canonical set
(`detection_engine/large_incoming_payment.py`, same statistics, canonical
columns) and its schema is now just another binding
(`config/bindings/legacy.yaml`, `config/profiles/legacy_local.yaml`) that
the one pipeline runs: `python -m agents.demo_fdm_scenario --profile
legacy_local`. `evaluation/evaluate.py` is kept as the only ground-truth
reader until R18's outcome backtest replaces it. History:
`docs/changelog.md` M25.

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
