# Current state (as of 2026-09-14)

Live and runnable end-to-end on the `offline_ollama` profile (local Ollama,
no credentials, no paid calls). Snowflake is wired but **NOT RUN**.

**Two parallel builds now exist in this repo**, deliberately kept
separate (see "FDM-aligned build" below for why):

1. The original legacy-schema pipeline described in the rest of this file
   (`clients/accounts/transactions/...`, `large_incoming_payment`,
   `external_macro_event`) — unchanged, still green.
2. A new FDM-shaped build against NatWest's Federated Data Model
   (`docs/fdm_reference.md`, `docs/decision_record.md`), additive
   alongside the legacy pipeline, not replacing it. See "FDM-aligned build"
   below.

## Run it yourself

This section is kept current -- if a command below stops working, that's
a doc bug, file it as one rather than assuming the feature is gone.

### Legacy pipeline (original build, still green)

```bash
source .venv/bin/activate
python -m datainsights.cli                        # full run, top 15 narratives (~60s first time, ~7s cached)
python -m datainsights.status                      # what happened last
python -m datainsights.judge.run_sample 10          # offline-sampled semantic judge
python -m evaluation.evaluate                       # dev-diagnostic precision/recall
cat var/insights/digest_*.md                        # the actual RM digest output
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

### Tests

```bash
python -m pytest tests/ -v                 # 315 tests total; 6 are live-Ollama-gated and
                                            #   auto-skip if localhost:11434 isn't reachable
python -m pytest tests/ -q -k "not live"   # deterministic-only, ~309 tests, no Ollama required
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
  correct (no future leakage, verified by test), 9/9 tests passing,
  idempotent, cooldown-suppressing. Spec: `docs/detector_spec_large_incoming_payment.md`.
- **Ranking**: explicit magnitude+recency formula, `datainsights/ranking.py`.
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
- **Status view**: `datainsights/status.py`.

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

## M7 — Domain registry + sink interface (this session)

Closed the gap `docs/adding_a_new_domain.md` used to flag honestly as
not-yet-true: adding a domain no longer means editing
`agents/orchestrator.py`, `agents/domain_agent.py`, or
`datainsights/correlation/hypothesis.py`. Two registries, deliberately
split by what they hold (see each module's own docstring for the full
rationale):

- **`datainsights/domain_registry.py`** (data) — product codes, allowed
  RM actions, and per-signal-type NBA category + hypothesis, loaded from
  new `config/domains_fdm.yaml`. Self-loading (not passed a rules dict)
  so `datainsights/correlation/hypothesis.py`'s `assemble()` gives real
  category/hypothesis text even when imported with no `agents/*` import
  anywhere in the chain, matching `tests/test_correlation.py`'s existing
  import shape.
- **`agents/domain_registry.py`** (code) — a `DomainSpec` per domain
  (its tool factory + `tool_name -> detector module` map), registered by
  one `register()` call at the bottom of `agents/tools.py`.
  `agents/orchestrator.py` now builds `DETECTOR_BY_TOOL` and the per-run
  domain-tools dict generically from this registry instead of a
  hardcoded dict/literal. "exogenous" keeps one documented exception (its
  factory needs an extra `event` argument).

Verified: `tests/test_domain_registry.py` registers a throwaway "widgets"
domain (both halves) and proves `DomainAgent`, `orchestrator`'s
`all_specs()`/`detector_by_tool()`, and `hypothesis.category_for`/
`hypothesis_for` all pick it up with zero edits to the three files above.
Full suite (244 tests, up from 239), both demo scripts
(`demo_fdm_scenario.py`, `demo_multiagent_scenario.py` against live
qwen2.5:7b), and a Streamlit `AppTest` smoke run were all re-executed
after the migration and produce byte-identical results to before it.

**Sink interface, introduced alongside** (per D1/D2 -- DataInsights
enriches signals, it doesn't arbitrate or deliver): `datainsights/sinks/base.py`
holds the boundary constants every sink enforces (`user_story_id` <=30
chars, `crm_text` <=200 chars, snake_case->dot.case), and
`datainsights/sinks/key_mapping.py` is the one isolated D7 boundary
module (`PRTY_ID -> prophet_party_id` / `-> Enterprise Customer ID`) --
identity-passthrough mocks, NOT RUN as real mappings. New
`datainsights/sinks/pega_event_mock.py` mocks
`SFPGMOD004_DS_PEGA_ORGANISATION_EVENT` (Tab 4's documented fallback
insertion point if the MIMO route isn't taken) -- schema-only, same NOT
RUN discipline as `mimo_placeholder.py`. `Recommendation` gained a
`recommendation_id` field (deterministic hash of prty_id + category +
signal_type + evidence_ref) for future feedback-capture joins (D6:
schema only, no propensity model). `tests/test_sink_contract.py` proves
the RM worklist, MIMO JSON, and Pega event mock all render the same
category/hypothesis/recommended_action/recommendation_id from one
Recommendation -- no sink computes its own.

Not attempted as a rigid one-method `Sink` Protocol: `fdm_worklist.py`'s
worklist needs `source` for client segment/sector/country context that
mimo/pega have no use for, so forcing one call signature across all
three would either strip that context or bloat the others with an
unused parameter -- deferred rather than done badly.

**Loose end from an earlier session, now half-resolved (see "Agentic
plan" section below, phase A4)**: `detection_engine/rating_downgrade.py`
is now wired -- `agents/tools.py`'s `make_risk_tools` + a `risk:` block
in `config/domains_fdm.yaml`, registered exactly like every other domain
(no edit to orchestrator.py/domain_agent.py/hypothesis.py). Verified on
real generated data: whole-book RISK_REVIEW count went from 2 to 3, a
genuine downgrade correctly suppressing a revenue recommendation
(`tests/test_risk_domain_wiring.py`). `pd_migration.py` is still
**not wired** -- it needs `PARTY_METRIC`, which the generator and
contract genuinely don't have (documented as deferred, no DDL captured);
wiring it would mean inventing a table, exactly what
`docs/adding_a_new_domain.md` says not to do. It stays inert until that
data exists, not a bug.

## M7 Step 2 — SLOT E2 baseline opt-in (this session)

`cash_buildup` and `revenue_pattern_change` -- the two "rolling/
time-series" detectors (docs/adding_a_new_domain.md's own family split;
the five "point-in-time snapshot" detectors have no history-of-a-metric
shape to baseline and were correctly left alone) -- gained a
`baseline: deterministic | isolation_forest` config
(`config/rules.yaml`, default `deterministic`, behaviour byte-identical
to before this option existed -- verified by re-running both demo
scripts and diffing output). `isolation_forest` swaps the "expected/
prior" value for `datainsights.ml.baselines.IsolationForestBaseline`'s
outlier-robust estimate over the agreement's own history; the flagging
rule (`increase_pct >= min_increase_pct`, etc.) is unchanged -- only
what counts as "the baseline" changes. Every detection row and the
downstream `Recommendation.baseline_source` field carry which baseline
produced it, surfaced in the RM digest (`_...· baseline: `deterministic`_`
per row) -- the traceability this milestone asked for.

New `tests/test_baseline_wiring.py` (9 tests): as-of correctness (a
future observation added to the input frame must never change an
earlier row's result -- proven for both detectors), insufficient-history
handling (never a guessed value below `IsolationForestBaseline`'s
`min_observations=8` floor), and that the deterministic default's output
now correctly labels itself `"baseline": "deterministic"`. Full suite:
**253 passed** (was 244).

**Executed, real result (first run)** -- `python -m datainsights.ml.compare_baselines`
ran both baselines through the actual detectors against the real
generated FDM dataset, evaluating one point per agreement (the latest,
matching how `agents/tools.py` actually calls these detectors in
production): challenger detected **0/54** on revenue_pattern_change
against the deterministic baseline's 14, and 0/60 on cash_buildup. Full
detail (superseded below) kept the discipline point: `IsolationForestBaseline`
refit a fresh, unbounded, 100-tree sklearn model per evaluated point,
measured at ~4s per ~90-row agreement history.

**Corrected after a real performance fix (same session) -- the earlier
"0 detected" result partly reflected the un-tuned model, not the
challenger's ceiling.** Two changes to `IsolationForestBaseline`
(`datainsights/ml/baselines.py`), root-caused by a direct micro-benchmark
rather than guessed: (1) `max_history=30` -- bound each fit to the
trailing 30 observations before `as_at`, not the client's entire
history; (2) `n_estimators=20`, down from sklearn's default 100 --
the micro-benchmark showed **tree count, not sample size, is the actual
cost driver** (90 vs 30 samples cost the same ~3.3s for 92 fits;
cutting trees 100 -> 20 cut that to ~0.7s). Measured on the same
92-row agreement: **3.9s -> 1.1s** (3.5x). New
`tests/test_baseline_wiring.py::test_isolation_forest_max_history_bounds_the_fit_window`.

Re-running `compare_baselines.py` after the fix changed the *comparison
result itself*, not just its speed -- disclosed here rather than left
stale:

| Detector | deterministic detected | isolation_forest detected | disagree |
|---|---|---|---|
| cash_buildup (60 agreements) | 0 | 0 | 0 |
| revenue_pattern_change (54 agreements) | 14 | **9** | **11** |

The bounded window and fewer trees changed which points the forest
flags as outliers -- the challenger now genuinely disagrees with the
deterministic baseline on 11 of 54 agreements (5 it flags that
deterministic doesn't, 6 the reverse), instead of detecting nothing.

**Verdict, still mechanically derived, updated**: still keep
`deterministic` as the production default -- disagreement isn't evidence
of improvement, and nothing here has been checked against an outcome
(both are unsupervised heuristics on synthetic data). But the earlier
"the challenger can't clear `min_observations=8` at this scale" framing
is no longer the accurate reason to reject it -- it clears the floor
often enough to disagree meaningfully now. The open question going into
Step 3 is which of the 11 disagreements would be right on real
resolution data, which this dataset cannot answer (no real outcome
labels exist to check against, and `protected_evaluator_only/`'s labels
are off-limits to detector logic by construction, per CLAUDE.md).

## Agentic plan execution (this session) -- see docs/agentic_plan.md

Phases A0, A1, and A4 from the plan are built and verified against real
generated data / real local Ollama. A2 (investigator) and A3 (RM
copilot) are not started. A5 (feedback learning) is correctly not
started -- the plan itself says not to until RM-response label access
exists (D6).

**A0 -- honest agent, audit trail.** `agents/domain_agent.py`'s
`DomainAgent.evaluate()` no longer gives the Strands `Agent` its tools --
every detector already ran once, deterministically, before the LLM call;
handing the LLM the same tools meant it ran them AGAIN itself (the old
system prompt said "call every tool"), for zero effect on the validated
output (`_validate()` only ever checked the first, deterministic run).
Removed. Measured: the one-client live demo dropped from ~24s to
**16.7s**, same category/hypothesis/sizing output. New
`datainsights/agent_trace.py` (its own small SQLite table, independent
of the legacy pipeline's `datainsights/state.py`) records
`(prty_id, domain, narrative_source, latency_seconds, fell_back)` per
narrated domain, keyed on `recommendation_id` -- verified: three domains
narrating for one client all land under the same recommendation_id, and
a fallback narration correctly sets `fell_back=1`. This is the AgentCore
Phase 2 "audit trail" / "data lineage" requirement satisfied by
construction, not retrofitted. 12 new tests
(`tests/test_agent_trace.py`).

**A1 -- event extraction agent (the plan's one genuinely new capability).**
New `external_events/event_extraction_agent.py`: local-Ollama structured
extraction of an `ExogenousEvent` from raw text, gated by `_validate()`
-- NACE section must be in the set this dataset's generator actually
uses, country a 2-letter code, date parseable and not after ingest_date,
`event_type` from a fixed known set, the cited `quote` must be a real
substring of the source text (no fabricated citation), and the claimed
EUR value must be traceable to a number in the quote **tagged as EUR,
not another currency** (see below). Anything failing lands in a review
queue (`external_events/extracted_event_store.py`), never silently
promoted. Exposure qualification (`exposure_qualifier.qualifies()`)
is untouched -- this agent only produces the event, it decides nothing
about any client.

Executed against 10 hand-authored notices (not generator output, not
`protected_evaluator_only/`, disclosed as synthetic in the test file) with
real qwen2.5:7b:
- First run: **0/6 real events extracted** -- the model invented its own
  wording for `event_type` ("tender", "contract_award", ...) instead of
  the one literal string the system prompt implied but never stated.
  Fixed by listing the exact allowed value in the prompt.
- Second run surfaced a second real bug: a GBP-denominated notice was
  extracted with `estimated_value_eur` set to the GBP figure -- the
  quote happened to also contain the word "EUR" (in "no EUR figure
  given", a negation 60+ characters away), which a naive substring
  check accepted. Fixed with a proximity-based check (currency marker
  within 12 characters of the specific number claimed, not "EUR appears
  anywhere in the quote").
- Final run: **TP=5, FP=0, FN=1** (of 10). The one miss was a genuine
  model numeric slip (reported "900" instead of "900,000" from "EUR
  900,000") that validation correctly caught and routed to review rather
  than accepting a wrong figure. **Zero false positives across every
  run** -- the hard boundary (nothing structurally wrong ever reaches
  "extracted") held throughout, including while recall was still 0%.
  `tests/test_event_extraction_agent.py`, `tests/test_extracted_event_store.py`.

**A4 -- Risk domain wired (the maintainability payoff).** Registered
`rating_downgrade` as a real domain through the M7 registry --
`agents/tools.py`'s `make_risk_tools` (reads `PARTY`'s real bi-temporal
`RSK_GRD_CD`/`RSK_GRD_VAL` history, already in the generated dataset) +
a `risk:` block in `config/domains_fdm.yaml`. Zero edits to
`orchestrator.py`, `domain_agent.py`, or `hypothesis.py` -- exactly the
claim `docs/adding_a_new_domain.md` now makes. Hit the exact bug that
doc warns every new tool hits once: the tool's evidence dict omitted
`grade_effective_date`, breaking `to_signal()` silently until the
whole-book demo crashed with a `KeyError` -- fixed, one line. Verified
on real data: whole-book RISK_REVIEW count went **2 -> 3**, one genuine
downgrade correctly suppressing what would otherwise have been a
revenue-category recommendation (rule 1). `pd_migration` stays
unwired -- it needs `PARTY_METRIC`, which doesn't exist in this dataset;
wiring it would mean inventing a table. 3 new tests
(`tests/test_risk_domain_wiring.py`).

Full suite after all three phases: **275 passed** (2 live-Ollama tests
deselected when Ollama isn't reachable; both pass when it is). Both
demo scripts and the Streamlit dashboard re-verified working.

## A1 wired into the flow, Step 3, A2, A3 (this session, continued)

**A1 wired in.** `agents/demo_fdm_scenario.py` now prefers
`external_events/output_fdm_extracted/extracted_events.csv` (A1's own
output shape) over the generator's fixture, falling back to the fixture
if nothing's been extracted yet -- so the demo still runs standalone.
New `external_events/ingest_notices.py` + `sample_notices.py`: one
hand-written notice, deliberately matching the fixture's (sector,
country, value) so the SAME positive/negative proof (PRTY00036/PRTY00037)
still holds -- verified: identical 24-recommendation, same-category
output, now genuinely sourced from a sentence instead of a hand-typed
CSV row. (First attempt at a longer, more detailed notice failed
structured-output entirely -- simplifying back to the golden-set's
proven style fixed it; disclosed rather than hidden.)

**SLOT E2 performance, root-caused not guessed.** A direct
micro-benchmark (not the earlier "must be sample size" assumption)
found the actual cost driver in `IsolationForestBaseline`: tree count
(`n_estimators`, sklearn default 100), not history length -- 90 vs 30
samples cost the same ~3.3s for 92 fits; cutting trees 100→20 cut that
to ~0.7s. Fixed in `datainsights/ml/baselines.py`: `n_estimators=20` +
`max_history=30` (bounds each fit to a trailing window, a genuine
scale-safety property distinct from the speed fix). Measured on the
same real agreement: **3.9s → 1.1s**. New
`tests/test_baseline_wiring.py::test_isolation_forest_max_history_bounds_the_fit_window`.
This changed the earlier "challenger detects nothing" comparison result
too (see the correction inline in that section above) -- disclosed
there rather than left contradicting this one.

**Step 3 -- scaled data + holdout, honestly re-measured after a false
alarm.** `data_generator/fdm/generate_fdm.py` gained `--n-parties` and
`--history-years` (both optional, defaulting to the original 60/2.5yr --
byte-identical output confirmed via diff when neither is passed). New
`data_generator/output_fdm_scaled` (300 parties, 4yr, seed 42) and
`_scaled_holdout` (seed 1337). New `datainsights/ml/scale_evaluation.py`
runs the whole-book pipeline + SLOT E2 comparison at this scale and
writes a versioned run manifest (`var/ml_runs/*.json`) -- the honest
substitute for "persist models": SLOT E2's baselines are stateless,
re-fit per call by design (never trained-and-saved), so there is no
model artifact to persist; what's versioned instead is the RUN --
rule_version, baseline hyperparameters, data fingerprint, as-of, result.

First run measured **1031s for 300 clients (3.4s/client, ~23x the
60-client rate)** -- a real-looking scaling cliff. It wasn't one:
profiling one client in isolation showed 0.39s, and a 30-client
sequential loop stayed flat at ~0.3-0.36s/client with no growth,
directly contradicting the full-run number. Re-running the exact same
script with no other processes competing for the machine gave **98.8s
(329ms/client)** -- the original run was contended by other work this
session had running concurrently, not a code problem. Stated here
because reporting the first number without checking it would have been
exactly the kind of unverified claim CLAUDE.md's working-style section
warns against.

Clean, final numbers (dev seed 42 vs holdout seed 1337, both 300
clients, 4yr history):

| | dev | holdout |
|---|---|---|
| whole-book pipeline | 98.8s (329ms/client) | 99.8s (333ms/client) |
| recommendations | 75 (49 FINANCING_NEED, 21 ADVISORY_ONLY, 5 RISK_REVIEW) | 91 (70/15/6) |
| SLOT E2 comparison runtime | 35.4s | 32.7s |
| revenue_pattern_change disagree | 51/291 (det=48, iso=23) | 48/275 (det=70, iso=58) |
| cash_buildup disagree | 0/300 | 0/300 |

Consistent behaviour across two independently-seeded datasets (similar
runtime, similar disagreement rate) -- a genuine, if modest, reassurance
that nothing here is wildly overfit to one seed. No verdict change:
still nothing to check either baseline's disagreements against (no real
outcome labels exist on synthetic data), so "keep deterministic as
default" stands, now on a 5x-larger, honestly-measured base.

**A2 -- Investigator agent, for `fixed_rate_expiry`'s disclosed
simplification.** `config/domains_fdm.yaml`'s `fixed_rate_expiry` block
gained `ambiguous: true` + `category_options: [TREASURY_OPPORTUNITY,
HEDGING_NEED]`; `datainsights/domain_registry.py` gained
`is_ambiguous()`/`category_options()`; `Recommendation` gained
`ambiguous: bool`. New `agents/investigator_agent.py`: three read-only,
single-client-scoped tools (`get_client_context`, `get_currency_exposure`,
`get_recent_balance_trend`) and `investigate()`, which never changes the
Recommendation's category itself -- only proposes a refinement from the
fixed `category_options` set for an RM to confirm.

No client in the 60-person dataset currently has an active
`fixed_rate_expiry` detection (checked directly) -- tested against a
hand-built Recommendation using a real client's other data instead.
**A live run surfaced a real reasoning bug the validator didn't catch**:
the model proposed `HEDGING_NEED` while its own stated evidence was "no
multi-currency activity found" -- which supports the opposite
conclusion. The category itself was still valid (in the allowed set),
so the original validation passed it; the contradiction is in the
reasoning, not the category. Documented here rather than silently
patched -- **not yet fixed** (a `_direction_problem`-style consistency
check, same pattern `agents/domain_agent.py` already uses for narrative
direction, would catch it; not built this pass). Treat A2's proposals as
genuinely unconfirmed pending that fix, not as reliable yet.
9 new tests (`tests/test_investigator_agent.py`, 1 live).

**A3 -- RM copilot + feedback capture, in the actual Streamlit dashboard.**
New `agents/rm_copilot_agent.py`: answers an RM's question using ONLY
the fields already on that one worklist row -- no tool, no DataSource
access, structurally unable to reach another client's data (tighter
scope than A2's tools, deliberately, since this is the one agent an RM
talks to directly). Same validate-or-fallback discipline; one real bug
found and fixed during testing -- the numeric-traceability check only
scanned numeric-typed row fields, so a legitimate restatement of
`sizing_basis`'s embedded "25pct" was flagged as fabricated; fixed to
also scan string fields for embedded numbers.

New `datainsights/rm_feedback.py`: the live Customer Engaged / Not
Appropriate / Remind Me Later taxonomy, captured against
`recommendation_id` -- the actual start of the D6 feedback loop, schema
only, no propensity model reads it. `RM_WORKLIST_COLUMNS` gained
`recommendation_id` and `ambiguous` so the dashboard has both to work
with. Wired into `dashboard/app.py`'s "FDM worklist" page, directly
below the existing "prepare for the client call" section.

**Verified end to end via Streamlit's `AppTest`, not just page load**:
typed a real question into the copilot, clicked Ask, got a live
qwen2.5:7b answer grounded in the row ("sized at approximately EUR
800,000... 25% of the illustrative event value... evidence reference
EVENT_FINANCIAL:AGR000060"), no exception; selected "Customer Engaged",
clicked Save, confirmed the row actually landed in
`var/rm_feedback.db`. 14 new tests
(`tests/test_rm_feedback.py`, `tests/test_rm_copilot_agent.py`, 1 live).

## What's next -- see docs/generalization_plan.md

The reviewed plan for making this generic across data models (canonical
semantic model + per-schema bindings), user-registrable exogenous event
types (declarative registry + `EventSource`), and local <-> AgentCore
portability (profile-driven runtime, S3/parquet + Glue/Snowflake
adapters, state/artifact stores, OTEL, MCP) is `docs/generalization_plan.md`.
It is grounded in a code survey (section 1 of that file cites file:line
for every coupling it found) and is written to be executed phase by phase
by another model. Nothing in it is built yet.

## M9 -- Generalization: Phase 0 done, Phase 1 foundation built (this session)

Executing `docs/generalization_plan.md`. Full plan is a multi-week build
(Phase 1 alone is estimated 1-2 weeks for the complete detector rewire);
this session did Phase 0 completely and Phase 1's foundation, verified,
without touching a single detector -- stated honestly rather than
claiming more.

**Phase 0 -- runtime factory, DONE.** `datainsights/runtime.py`'s
`build_runtime(profile)` is now the single composition point;
`datainsights/config.py` widened (backward compatible -- both legacy
profiles still validate unchanged) to accept `s3_parquet`/`glue_athena`
source backends, `dynamodb` state, `s3` output, and `model_gateway` LLM
provider -- each constructs and validates, then raises `NotImplementedError`
naming the exact blocking reason when actually used, never a silent
local fallback. New `config/profiles/fdm_local.yaml`. Six hand-wired
entry points refactored: both demo scripts (now take `--profile`),
`agents/entrypoint.py` (also fixed a real staleness bug -- its
`TOOL_FACTORIES` dict only had `deposits`/`lending`, silently missing
`risk` since M8; it now reads `agents.domain_registry.all_specs()`),
`datainsights/ml/compare_baselines.py` and `scale_evaluation.py`,
`external_events/ingest_notices.py`, `dashboard/app.py`'s copilot model
construction. Regression-verified byte-identical: both demos, the ML
comparison script, and the Streamlit dashboard produce the exact same
output as before the refactor. 13 new tests
(`tests/test_runtime.py`, `tests/test_entrypoint.py`).

**Phase 1 foundation -- canonical semantic model, built and tested;
detector rewire NOT started.** New `config/semantic_model.yaml` (7
concepts: Party, Account, BalanceObservation, Transaction,
RiskGradeVersion, PartyMetricVersion, CollateralValuation) and
`config/bindings/fdm.yaml` (the FDM schema's mapping onto them --
renames, value maps, bi-temporal columns, single- and two-hop joins).
New `datainsights/semantic/` package: `binding.py` (typed loader),
`canonical.py` (`CanonicalSource` -- reads any `DataSource` through a
binding, returns canonical column names only), `validate.py`
(config-time contract checking, catches a bad mapping before any data
is read).

Verified against real generated data, not just unit-tested in isolation
-- and it found real bugs, disclosed rather than smoothed over:
- A YAML 1.1 gotcha: bare `on:` (the join-key field) parses as the
  boolean `True`, silently. Fixed by quoting it in the binding file.
- `_project()` initially only mapped a concept's own `fields`, missing
  every column that arrives via a `joins` entry (e.g. `Party.sector_code`
  from the `PARTY_DEMOGRAPHIC` join) -- caught immediately when reading
  real `Party` data returned no sector/country columns at all. Fixed.
- The validator initially checked every join's key against only the
  BASE entity's columns, rejecting `CollateralValuation`'s real,
  necessary two-hop join (`COLLATERAL_ITEM_VALUE` -> `AGREEMENT_COLLATERAL_ITEM`
  -> `AGREEMENT`, where the second join's key only exists after the
  first runs). Fixed by tracking columns as they accumulate across
  joins, matching `CanonicalSource`'s actual left-to-right merge order.

12 new tests (`tests/test_semantic_bindings.py`), including an as-at
proof (a future version must never leak backward) and a real bi-temporal
read matching PRTY00036's actual CCC->C grade downgrade already
detected by `rating_downgrade.py`. Full suite: **315 passed** (was 296).

**What Phase 1 still needs, not attempted this session**: nothing in
`detection_engine/`, `agents/tools.py`, `external_events/exposure_qualifier.py`,
or `datainsights/fdm_worklist.py` reads through `CanonicalSource` yet --
every detector still requires physical FDM column names
(`AGRMNT_LDGR_BAL_AMT`, `FIN_EVNT_PSTD_DT`, etc.), exactly as
`docs/generalization_plan.md` section 1.2 catalogued. The second binding
(`config/bindings/legacy.yaml`, proving genericity against the schema
already in this repo) does not exist yet either. Both are real, scoped,
undone work -- see `docs/generalization_plan.md`'s Phase 1 for the exact
files and acceptance tests, now updated with this session's status.

**Phases 2-5 (event registry, cloud sources/AgentCore profile, ML/eval
standards, self-service onboarding): not started.** Correctly so --
each depends on Phase 1 completing per the plan's own sequencing
("Do not start Phase 1 until Phase 0's acceptance passes" / later phases
build on Phase 1's rewire). Attempting them against an unfinished
foundation would produce work built on a base still likely to change.

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
