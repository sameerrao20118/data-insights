# Current state (as of 2026-09-19)

Live and runnable end-to-end on the `fdm_local` profile (local Ollama,
no credentials, no paid calls). **One pipeline** since R23: the original
legacy CLI is gone; its schema is a binding the same pipeline runs.
Snowflake is a declared source, **NOT RUN**.

## At a glance — what is true right now

*History and evidence for every line here: `docs/changelog.md` (M7–M26).
Plan being executed: `docs/refactor_plan.md` (status block under its wave
table). Audit that produced the plan: `docs/hardcoding_audit.md`.*

**Pipeline.** One deterministic fan-out/fan-in per client
(`agents/orchestrator.py::evaluate_client`): 8 registered detectors in 4
domains read through `CanonicalSource` + a per-schema binding
(`config/bindings/{fdm,legacy,sba}.yaml`), emit canonical `Signal`s,
`assemble()` picks category/hypothesis/size — consulting the cross-domain
`combinations:` table first (R10) — and risk suppression applies last.
The whole-book path shares one `CanonicalSource` (R13): 300 clients in
4.6 s, ms/client flat with book size. One bad client is recorded, never
fatal (R15). Narrated runs add Tier 2: the investigator proposes on
ambiguous / multi-domain recommendations, never decides (R9).

**Everything the business would edit is config.** Categories
(`config/categories.yaml`, R2), signal→category/hypothesis/why-now
(`config/domains_fdm.yaml`), cross-domain rules (same file), exogenous
event types (`config/event_types.yaml`), rules/thresholds
(`config/rules.yaml`, per-currency; per-schema overrides in the binding,
R6), the semantic registry and domain pack (`config/semantic_model.yaml`,
`config/packs/banking.yaml`, R4), ML policy + power criteria
(`config/ml_policy.yaml`, R7), the model id and who is signed in
(`config/profiles/<name>.yaml` only, R20/R21). Adding a category, a
rule or a domain is a YAML edit — `docs/adding_a_new_domain.md`; adding
a schema is `onboarding.propose` → review → `onboarding.accept` (binding,
contract and profile, R8).

**One pipeline, three schemas, any currency.** R23 retired the legacy CLI;
`python -m agents.demo_fdm_scenario --profile {fdm_local|legacy_local|sba_local}`
runs the same code (36 / 167 / 131 recommendations). Amounts are in the
account's currency end to end (SBA is USD, R17). The worklist is scoped
server-side from the signed-in principal (R21).

**Guards that fail the build.** ruff + CI (R16); every declared config
field has a reader or is on the not-yet-enforced register (R14); no
FDM physical name outside `datainsights/sources/` (R3); every category
referenced anywhere is declared; every combination rule names real
signals; no model id outside the profiles; the dashboard never lists
protected data; detectors have canonical `REQUIRED_COLUMNS` (R1).

**ML, honestly.** SLOT E2 is an outlier-robust per-entity statistic, not
ML in the model-risk sense; on every shipped dataset the ML tab says *no
ML challenger is eligible* and names the criterion (92 entities < 200;
0 RM outcome labels < 100). The plumbing for a real cross-entity model
(policy, registry, label pipeline) exists and waits for labels.

**Signal discovery, in shadow.** Until recently every signal in
`config/domains_fdm.yaml` was hand-authored — the system could execute
signals a person had named, but nothing could propose a new one from the
data. `onboarding/signal_*.py` now enumerates candidate rules over
canonical fields, screens them for NOVELTY (not value — there are no RM
outcome labels to measure value with), and asks local Ollama to name and
categorise the survivors. Accepted proposals land in
`config/domains_discovered.yaml` as `status: shadow`:
`datainsights/domain_registry.py` does not read that file, so
`correlation/hypothesis.py::assemble()` cannot resolve their category and
they cannot reach an RM worklist. Run on legacy data: 28 candidates, 3
passed screening, 3 accepted to shadow, 0 promoted. Scope was
recommendations 1-8 of 15; 10-15 (SQL/Spark executors) are not started.
See `docs/signal_discovery_design.md` for the full scope table and
`docs/HANDOFF_signal_discovery.md` to resume the work.

**Suite:** run `pytest tests/ -q -k "not live"` — ~770 tests, ~1 min, no
model needed. The dashboard's ten pages each render under AppTest in it. Live tests need local Ollama.

**Not yet (Wave 3 and beyond):** Wave 3 (semantic registry + domain packs, per-binding rules,
currency, profile proposer, entitlement, retire the legacy pipeline) and
Wave 4 (run lock, incremental runs, event ingestion, more sources,
outcome backtest) are not started. Snowflake has never executed a query.
Signal discovery recommendations 10-15 (one spec, three executors;
pushdown planner; cross-executor conformance suite) are not started.

## Run it yourself

This section is kept current -- if a command below stops working, that's
a doc bug, file it as one rather than assuming the feature is gone.

### The legacy schema, through the one pipeline (R23)

```bash
source .venv/bin/activate
python -m data_generator.generate_data                        # the legacy-schema dataset (config/bindings/legacy.yaml)
python -m agents.demo_fdm_scenario --profile legacy_local     # 606 clients; large_incoming_payment fires here
```

### FDM-aligned build -- generate data first

```bash
python -m data_generator.fdm.generate_fdm --seed 42          # dev, 60 parties, ~2.5yr history (default)
python -m data_generator.fdm.generate_fdm --seed 1337         # holdout, same defaults
python -m data_generator.fdm.generate_fdm_events               # matching exogenous event fixture

# Scale knobs (M8 Step 3) -- both optional, defaulting to the above:
python -m data_generator.fdm.generate_fdm --seed 42 --n-parties 300 --history-years 4 \
    --out-dir data_generator/output_fdm_scaled
python -m data_generator.fdm.generate_fdm_events --fdm-dir data_generator/output_fdm_scaled \
    --out-dir external_events/output_fdm_scaled
```

### Run the pipeline

```bash
python -m agents.demo_fdm_scenario           # whole book, no LLM, ~9s on the 60-party default
python -m agents.demo_multiagent_scenario    # one client, live local Ollama narration, ~17s
streamlit run dashboard/app.py               # interactive dashboard -- Overview / Data sources /
                                              #   Run the pipeline / Worklist / FDM worklist (RM copilot +
                                              #   feedback capture live here) / Digests / Status
```

Both demo scripts (and `agents/entrypoint.py`'s `invoke()`) read a
**profile** now (`datainsights/runtime.build_runtime()`, M9/Phase 0 --
see `docs/generalization_plan.md`), defaulting to `config/profiles/fdm_local.yaml`.
Pass `--profile <name>` to point either demo at a different profile
(e.g. one over the 300-party scaled dataset) instead of editing a
hard-coded path. `demo_fdm_scenario.py` writes
`var/insights/fdm_rm_worklist.csv`, `fdm_rm_digest.md`, and
`fdm_insights.json` (MIMO-shaped, schema-only). It prefers an
agent-extracted event over the profile's configured event source if one
exists -- see the A1 ingestion command below.

### A1 -- turn notice text into a validated event

```bash
python -m external_events.ingest_notices
# writes external_events/output_fdm_extracted/extracted_events.csv (validated)
#    or .../review_queue.csv (failed validation -- read this, don't promote it manually)
```

Edit `external_events/sample_notices.py` to try your own text; anything
that fails validation lands in the review queue, never silently in the
events file. Re-run `demo_fdm_scenario.py` afterward to see it flow
through.

### SLOT E2 -- ML baseline comparison

```bash
python -m datainsights.ml.evaluate_baselines        # controlled injection experiment (synthetic anomalies)
python -m datainsights.ml.compare_baselines         # real detectors vs real generated data, one point/agreement
python -m datainsights.ml.scale_evaluation --also-holdout   # same, at the 300-party/4yr scale + a run manifest
```

`compare_baselines.py`/`scale_evaluation.py` need the FDM data (and, for
the scaled variants, the `--n-parties`/`--history-years` generation step
above) already run. `scale_evaluation.py` writes a versioned run
manifest to `var/ml_runs/*.json`.

### Self-service schema onboarding (Phase 5a, CSV input only)

```bash
python -m onboarding.propose <data_dir> --name <schema_name>   # profile + LLM proposal, writes onboarding/proposals/<schema_name>/
cat onboarding/proposals/<schema_name>/review.md                # confidence/evidence/rejected-mappings report -- read before accepting
python -m onboarding.accept <schema_name>                       # human-gated: validates, then writes config/bindings + config/entities_<schema_name>.yaml
python -m onboarding.accept <schema_name> --force                # overwrite an existing schema of the same name
```

Or via the dashboard's "Onboard" tab: profile + propose → review/edit
the proposed YAML inline → accept. `onboarding/` never writes to
`config/` until `accept()` is explicitly run; `propose` alone is fully
side-effect-free outside its own `onboarding/proposals/` scratch
directory (gitignored).

### Tests

```bash
python -m pytest tests/ -v                 # 434 tests total (was 315); several are live-Ollama-gated
                                            #   and auto-skip if localhost:11434 isn't reachable
python -m pytest tests/ -q -k "not live"   # deterministic-only, no Ollama required
python -m pytest tests/test_onboarding_live.py -v   # separate live blind-test proof against real SBA data (~3 min)
```

## What's implemented and verified working

- **Data**: dev dataset (seed 42) + an independently-seeded holdout (seed
  1337), both with `trigger_events.csv` isolated under
  `protected_evaluator_only/`.
- **Config**: typed, validated profiles (`config/profiles/*.yaml`,
  `datainsights/config.py`). Hard-blocks paid calls, remote inference,
  Ollama `-cloud` model tags, and non-localhost endpoints at the code
  level, not just declaratively.
- **Source contract**: `config/entities.yaml` (transactions/accounts/
  balances only -- grain, PK, time/currency semantics, missing-data policy).
- **DataSource**: `OfflineLocalSource` (DuckDB-backed), tested against
  leakage (refuses `trigger_events`, refuses a protected-path root, refuses
  unbounded reads). `SnowflakeSource` skeleton exists, same interface,
  **NOT RUN** (no credentials).
- **Detector**: `detection_engine/large_incoming_payment.py` -- point-in-time
  correct (no future leakage, verified by test), cooldown-suppressing;
  since R23 a canonical detector in the deposits domain like every other.
  Spec: `docs/detector_spec_large_incoming_payment.md`.
- **Ranking**: retired with the legacy pipeline (R23); the worklist ranks
  by indicative revenue then signal strength (`datainsights/fdm_worklist.py`).
- **Narrative**: deterministic template baseline +
  local-Ollama structured-output narrator (qwen2.5:7b) with deterministic
  validation (schema, numeric consistency, banned-claim check) and
  fallback-to-template on any failure. Verified: a real failure case was
  caught and correctly fell back during development.
- **Judge**: offline-sampled, different model than narrator (llama3.1:8b),
  correlated-error caveat disclosed in code. Ran against 10 cached
  narratives: faithfulness mean 3.9/5 (min 2), 8/10 flagged for human
  review -- a genuine finding, not smoothed over. Worth reading those 8
  before trusting this narrator prompt further.
- **State**: SQLite, idempotent detections, narrative cache (evidence-hash
  keyed), run history. Verified idempotent on rerun (0 newly-seen on an
  unchanged snapshot).
- **Digest**: local markdown file under `var/insights/`, no delivery
  anywhere.
- **Evaluation**: separate module, reads the protected ground truth (its
  job, not the detector's). Dev-dataset result: precision 0.09 / recall
  0.66 vs. the narrow `TENDER_PAYMENT` label; holdout-dataset result:
  precision 0.14 / recall 0.83. Consistent pattern across both seeds. Low
  precision is expected and explained in evaluation/evaluate.py's
  docstring -- the detector's actual claim ("statistically large payment")
  is broader than that one injected label, so many detections are
  legitimate anomalies the generator never labeled as anything.
- **Status view**: the dashboard's Status page reads `var/agent_traces.db` (R23).

## FDM-aligned build (additive, alongside the above)

Built this session against `docs/fdm_reference.md` (NatWest's captured
Federated Data Model) and `docs/decision_record.md` (the captured
engineering decision record) — see those two files for full source
context. Kept **additive** rather than replacing the legacy pipeline:
`CLAUDE.md` says continue from verified work, and the decision record's
own Phase 2 (FDM migration) says it should follow a real Phase 1 (an
actual MIMO integration), which isn't reachable from a personal machine.
Scope, per an explicit decision with the user: **Deposits + Lending +
Exogenous domains only** — Risk (Domain 4) and Treasury (Domain 3) are
deferred (no captured column DDL for Risk; Treasury has no confirmed
client-facing data source at all per the decision record's own Phase 6
finding).

- **Schema + synthetic data**: `config/entities_fdm.yaml` (11 FDM
  entities, bi-temporal `EFFECTIVE_START/END_DT` throughout),
  `data_generator/fdm/generate_fdm.py` (60 parties, dev seed 42 + holdout
  seed 1337), `data_generator/fdm/code_domains.py` (every code value
  tagged `DOCUMENTED` or `INVENTED` — `invented_domains()` gives Phase 7
  SME validation an explicit correction list).
- **Source adapter**: `datainsights/sources/fdm_local.py` --
  `FdmLocalSource`, with a real as-at join helper (verified against an
  actual risk-grade change and an actual collateral valuation drop in the
  generated data, not just column presence) and FinCrime-table-reference
  refusal enforced in code (`FSA_PRD_FINCRIME`, `FSA_PRD_FC_ANALYTICS`,
  `PEP_PRS`, `*_XDO`).
- **7 new detectors**: `cash_buildup`, `dormancy`, `revenue_pattern_change`
  (Deposits); `facility_utilization_spike`, `facility_maturity_approaching`,
  `fixed_rate_expiry`, `collateral_coverage_drop` (Lending) — same
  `Config`/`DETECTION_COLUMNS`/`detect()`/`apply_cooldown()` shape as
  `large_incoming_payment.py`, plus a `to_signal()` adapter each
  (`detection_engine/signal.py`) feeding the new correlation layer.
- **Strands domain agents**: `agents/` — tool-calling agents (each
  detector exposed as a `@tool`), validate-or-fallback narrative
  discipline matching `ollama_narrator.py`, and a full AgentCore
  contract-and-mock scaffold (`agents/model_factory.py`'s `get_model()`
  swap point, `agents/entrypoint.py`, `Dockerfile` -- all NOT RUN as real
  AgentCore, per `CLAUDE.md`). Verified live against qwen2.5:7b this
  session (Ollama happens to already be installed and running on this
  Mac -- the inverse of the bank machine, where it's the stated blocker).
- **Exogenous exemplar**: `external_events/exposure_qualifier.py`
  implements the decision record's 3-step exposure qualification (sector
  → geography → genuine-exposure test), matched against a real
  data-derived `public_tender_award` event
  (`data_generator/fdm/generate_fdm_events.py`) — verified end to end
  against an actual exposed/unexposed party pair the generator found in
  its own output, not a hand-built fixture.
- **Correlation layer**: `datainsights/correlation/` — `SignalBus`,
  `hypothesis.assemble()` (the decision record's 4 composition rules:
  RISK_REVIEW suppression, multi-domain confirmation, exogenous
  alignment, decomposable strength score), `dedupe.py`. This is the one
  genuinely new capability with no prior equivalent in this repo (closes
  `docs/gap_analysis.md` Gaps 2 & 3). `HIGH_RSK_CUST_IND` ("the one
  permitted read") is verified to only ever suppress a category, never
  scale a score or appear in narrative text.
- **MIMO output shape**: `datainsights/sinks/mimo_placeholder.py` --
  schema-only, NOT wired to real MIMO/Pega, enforcing the 200-char
  `crm_text` and 30-char `user_story_id` hard limits from the decision
  record.
- **Tests**: 138 total (was 16 before this session) — all green,
  including a live end-to-end test against real qwen2.5:7b and an
  end-to-end correlation test using the actual generated dataset (not
  hand-built fixtures) for the tender-award/revenue-growth pairing.

Not yet done from the decision record's own phasing: Phase 0 (a named
C&I Decisioning stakeholder) and Phase 1 (one real MIMO insight against
the real platform) require the NatWest VDI and real people — no code
substitutes for them. See `docs/decision_record.md`'s phase table for the
full mapping.

**Added after initial review** (a stakeholder challenge on this build
surfaced two real gaps — worth stating honestly rather than glossing
over): the first demo script bypassed the actual Strands agents entirely
(called detector functions directly, for speed), and all FDM data sat in
one flat folder with no real domain segregation. Both fixed:

- **Domain-segregated data, contract-driven**: `config/entities_fdm.yaml`
  now tags every entity `domain: kernel` (Tier 1, used everywhere) or
  `domain: lending` (Tier 3 extension) per `docs/fdm_reference.md`'s own
  tiering — not an arbitrary split. `FdmLocalSource` resolves paths as
  `{data_dir}/{domain}/{physical_table}.csv`; a future Snowflake source
  honoring the same `domain` field maps it to a schema instead, with zero
  change above that layer. See `docs/adding_a_new_domain.md`.
- **An Exogenous domain agent**: `agents/tools.py::make_exogenous_tools`
  wraps `exposure_qualifier.qualifies()` as a tool, given the same
  `DomainAgent` treatment as Deposits/Lending (tool-calling, not
  autonomous — exposure qualification itself stays deterministic Python).
- **Two demo scripts, different purposes**: `agents/demo_fdm_scenario.py`
  (fast, ~1s, 60 clients, bypasses the LLM agents — the volume proof) and
  `agents/demo_multiagent_scenario.py` (slower, real Ollama calls, one
  customer — the actual "three agents look at the same customer, a
  deterministic layer combines their evidence" story). Run both for a
  stakeholder session; they answer different questions.
- **`docs/adding_a_new_domain.md`** — the step-by-step guide that didn't
  exist before, for Risk/Treasury or anything else added later.

## What's explicitly NOT built / NOT RUN

- Snowflake: no query has ever executed against your trial account.
- Replay/simulation mode with a virtual clock, and a real scheduler/monitor
  loop: not built this pass -- `run_once` with `--as-of` gets you a
  single point-in-time cut, which is the primitive replay would be built
  from, but the incremental-checkpoint/watermark machinery in project
  instructions section 10.1 isn't implemented yet.
- ML ranking challenger, retrieval, prompt optimization, AWS/Bedrock/
  AgentCore: correctly out of scope for this phase.
- A coding-process benchmark (project instructions section 9): not started.
- Golden conformance tests comparing OfflineLocalSource vs. SnowflakeSource
  output on identical data: not built (SnowflakeSource has never run, so
  there's nothing yet to diff against).

## Known limitations worth reading before trusting this further

- All development-time precision/recall/judge numbers come from a session
  that authored the injection logic. Contamination is disclosed everywhere
  it matters; don't cite these numbers as detection quality.
- Event-time replay only (booking_date doubles as detection-availability
  time) -- not true availability-aware backtesting.
- The MAD-multiplier threshold (6.0) and floor (5000 EUR-equivalent) are
  provisional defaults, not validated against any outcome.
- Judge/narrator correlated-error risk: both local Ollama models, unknown
  training-data overlap.
