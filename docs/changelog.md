# Changelog — milestone record (M7 → today)

Moved verbatim out of `docs/current_state.md` by R22 (`docs/refactor_plan.md` §6k): that file
had become a 1,600-line changelog and the next reader could not find the *state* in it. Each
section below is the record written at the time, with its measured evidence; nothing was
edited in the move. New milestones are appended here; `current_state.md` states what is true
now and points here for how it got that way.

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

## M9 -- Generalization: Phase 0 and Phase 1 both DONE (this session)

Executing `docs/generalization_plan.md`. This session completed Phase 0
and all of Phase 1, including the detector rewire and a second, real,
structurally different schema proving genericity -- not just the
semantic-model foundation.

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

**Phase 1 -- canonical semantic model AND the detector rewire, both
DONE.** New `config/semantic_model.yaml` (7
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
detected by `rating_downgrade.py`.

**The detector rewire, done.** The nine detectors in `detection_engine/`
and their existing unit tests were left completely untouched -- zero
changes. Instead, the layer that FETCHES data for them was rewired to
read through `CanonicalSource` and translate the generic canonical
columns back to the FDM physical vocabulary (`PRTY_ID`, `AGRMNT_ID`,
`AGRMNT_LDGR_BAL_AMT`, `FIN_EVNT_PSTD_DT`, ...) the detectors already
expect: `agents/tools.py`, `external_events/exposure_qualifier.py`,
`agents/orchestrator.py`, `datainsights/fdm_worklist.py`,
`agents/investigator_agent.py`, `agents/entrypoint.py`. Call sites that
depend on a concept a binding may not provide (`check_collateral_coverage`,
`check_rating_downgrade`, sector/geography matching, the high-risk-flag
check) now check `canonical.available(concept)` first and degrade to an
honest `not_available_under_this_binding` status instead of crashing.

**Second schema proven: `config/bindings/legacy.yaml`.** Maps the
pre-existing "legacy" pipeline's schema (`config/entities.yaml`,
`data_generator/output/`) -- deliberately different column names, no
bi-temporal versions, a different entity shape -- onto the same seven
canonical concepts. `RiskGradeVersion`, `PartyMetricVersion`, and
`CollateralValuation` are honestly marked `unavailable` under this
binding (that data isn't contracted in the legacy schema).
`tests/test_legacy_binding_end_to_end.py` (5 tests) is the actual "any
schema" proof: `make_deposits_tools`/`make_lending_tools` -- the SAME
functions, same file, zero edits -- run against real legacy data, detect
real signals, and the risk-domain tools report
`not_available_under_this_binding` rather than crash or fabricate.
`tests/test_no_source_specific_coupling.py` (10 tests) guards this
structurally: none of the five rewired call sites may construct
`FdmLocalSource` directly or hardcode an FDM physical entity name.

Full suite: **320 passed** (was 296 before this session). Both demo
scripts (`agents/demo_fdm_scenario.py`, `agents/demo_multiagent_scenario.py`)
re-run end to end after the rewire and reproduce the same recommendations,
sizing, and positive/negative exposure proof as before it -- the
multiagent demo's real Ollama narration included. The Streamlit
dashboard (`dashboard/app.py`) verified via `AppTest` with no exception.

## M10 -- Generalization: Phase 2's core DONE (declarative event registry)

**Phase 2 (R2) -- declarative event-type registry, exposure-check
library, `EventSource` ABC: core DONE, one scoped gap disclosed.**
`config/event_types.yaml` + `external_events/event_registry.py`
(load-time-validated: an unknown check name, a `correlation` block
missing `hypothesis`, or a bad `magnitude` reference fails at load with
the offending type/field named) + `external_events/exposure_checks.py`
(named-check library: `has_account`, `recent_signal`,
`currency_activity`, plus `register_exposure_check()` for bespoke
Python) now drive `external_events/exposure_qualifier.py`'s
`qualifies()` and `datainsights/correlation/hypothesis.py`'s `assemble()`
generically -- neither has an `if event_type == "public_tender_award"`
branch left. `datainsights/sources/event_source.py`'s `EventSource` ABC
+ `CsvEventSource` formalizes what `load_events()` already did by
convention (`tests/test_event_source_conformance.py`, 7 tests).

**Second event type, `fx_rate_move`, registered in YAML only and
proven real, not just unit-tested.** `tests/test_fx_exposure_end_to_end.py`
(3 tests) copies the real generated FDM directory to a temp path,
adds a few real-shaped USD transaction rows for one account (disclosed
in the test's own docstring -- the checked-in dataset is untouched, so
no other test's byte-identical assumption is at risk), and proves the
actual `qualifies()` pipeline: a party with real USD activity qualifies
with a magnitude derived from the event's own `payload.pct_change`; a
party without does not. `tests/test_event_registry.py` (9 tests) covers
the registry's load-time validation directly. `external_events/output_fdm/`
is normally gitignored (generated fixture output, like `tender_events.csv`)
-- `fx_events.csv` is the one exception, force-added (`git add -f`)
because it is hand-authored (like `external_events/sample_notices.py`),
not generator output, so a fresh clone has it without needing a
generation step.

**Regression oracle, re-verified after the rewire**: both demo scripts
re-run and reproduce byte-identical recommendations, sizing text, and
the tender positive/negative proof -- confirming the generalized
`qualifies()`/`assemble()` produce identical output to the pre-Phase-2
hardcoded branches for the one event type already in production use.
Full suite: **349 passed** (was 320 after Phase 1).

**Disclosed, scoped gap, not attempted this pass**:
`external_events/event_extraction_agent.py`'s `KNOWN_EVENT_TYPES` now
reads from the registry (so `fx_rate_move` is extraction-*eligible* in
principle), but `ExtractedEventFields`, its system prompt, and
`_validate()`'s grounding checks (quote substring, million/thousand
notation, currency-marker matching) are still shaped for
`public_tender_award`'s specific payload only -- no `fx_rate_move`
extraction has actually been attempted or validated. A dynamic
per-type payload model and matching grounding checks are real, undone
work; see `docs/generalization_plan.md`'s Phase 2 section for the exact
gap. `category_hint`-driven category override (sketched in the plan)
was not built either -- this pass's fx proof attaches to whichever
endogenous signal is already ambiguous toward the right category
(`fixed_rate_expiry`), which was sufficient and needed no new
mechanism.

**Phases 3-5 (cloud sources/AgentCore profile, ML/eval standards,
self-service onboarding): not started.** Phase 1 and Phase 2's core --
their prerequisites -- are now complete, so these are unblocked but not
attempted this session.

## M11 -- Local Ollama vs. AgentCore: proven as ONE code path, two profiles

The user asked, in effect: "for every implementation, I want two ways
to run it -- local Ollama, and AgentCore." The design already made this
true (every entry point calls `agents/entrypoint.py`'s `invoke()`; the
AgentCore-decorated handler is a one-line wrapper around it) but it had
never been mechanically PROVEN, and there was no checked-in profile
representing "the AgentCore-shaped configuration" someone could
actually select. Both gaps closed this session, scoped exactly to
"contract/mock only, no real AWS call" per the user's own choice.

**Proven, not just designed**: `BedrockAgentCoreApp.entrypoint()`'s own
installed source was read directly (not assumed) -- it registers the
decorated function unchanged and returns it as-is, so
`agents.entrypoint.agentcore_entrypoint` IS `invoke` under another name;
calling it starts no server and makes no network call.
`tests/test_entrypoint.py` adds three tests: `agentcore_entrypoint(payload)
== invoke(payload)` for an identical payload (byte-identical proof),
`app.handlers["main"] is agentcore_entrypoint` (the real registration,
not an assumption), and `build_runtime("fdm_agentcore")` raising
`NotImplementedError` naming its exact first blocking reason.

**New `config/profiles/fdm_agentcore.yaml`**: the AgentCore-shaped
counterpart to `fdm_local.yaml` -- `runtime.target: agentcore`,
`source.backend: s3_parquet`, `llm.provider: model_gateway`,
`state.backend: dynamodb`, `output.backend: s3`, `event_source.backend:
s3`. Running `python -m agents.demo_fdm_scenario --profile
fdm_agentcore` validates every field (all of `datainsights/config.py`'s
checks pass) and constructs as far as it honestly can, then raises a
plain, uncaught `NotImplementedError` at `source.backend='s3_parquet'`
-- verified by actually running it, not just reading the code. This is
correct, disclosed behavior, not a bug to fix: real S3/Glue/DynamoDB
adapters and any actual deployment are Phase 3 work, contract-and-mock
only until explicitly authorized (`CLAUDE.md`).

Full suite: **352 passed** (was 349). README updated with a "Local
Ollama and AgentCore are the same code, two profiles" section.

## M12 -- Phase 2's disclosed gap closed: extraction is now fully declarative too

`external_events/event_extraction_agent.py` no longer hardcodes a
schema for `public_tender_award`. `config/event_types.yaml` gained an
`extraction:` block per type (which of ExogenousEvent's fixed fields to
extract, plus `pattern`/`enum`/`min`/`max`/`grounded_in_quote`/
`currency`/`optional` metadata on both those and `payload` fields).
`_build_fields_model()` builds a pydantic model per type via
`create_model`; `_build_extraction_prompt()` generates the system
prompt from the same schema; `_validate()` is one generic function
driven by it -- no per-type branch anywhere in the module. Extraction
is now two LLM calls: classify (which registered type, if any?) then
extract (that type's own dynamic model).

**A real reliability bug found and fixed via live testing, disclosed
rather than hidden**: a bare `tools=[]` Agent forcing a small
structured-output schema intermittently returned NO tool call at all
with this local model (qwen2.5:7b via ollama) -- `strands`' own
"ToolChoice ... not supported and will be ignored" warning is the
proximate cause; ollama doesn't actually enforce forced tool-choice, so
an empty response is possible. Reproduced consistently in isolation,
fixed by registering one harmless placeholder tool (`_noop`) on both
the classify and extract agents -- verified stable across three
consecutive live runs after the fix (was failing on most attempts
before it). A one-retry fallback remains as a second line of defense.

**Verified live, not just built**: the golden-set test now spans both
event types (12 notices: 6 tender, 2 fx, 4 negative) and passed with
**TP=7, FP=0, FN=1, TN=4** -- including a real `fx_rate_move`
extraction from free text ("The EUR/USD pair fell 6% against the
dollar over the past month...") producing a correctly-typed,
correctly-grounded event with `payload={currency_pair: "EUR/USD",
currency: "USD", pct_change: -0.06, window_days: 30}`. The grounding
check itself needed generalizing beyond million/thousand notation to
percent-as-fraction ("fell 6%" grounding a stored value of `-0.06`) --
a real case the tender-only design never needed to handle, caught by
writing the fx test fixtures before the live run, not by the live run
itself. `python -m external_events.ingest_notices` re-run end to end:
same tender extraction, same output as before this change.

Full suite: **359 passed** (was 352, plus one additional live-only test
now deselected under `-k "not live"`) --
`tests/test_event_extraction_agent.py` rewritten and expanded to cover
both event types (was 15 deterministic + 1 live test, now 21
deterministic + 2 live tests). `docs/generalization_plan.md`'s Phase 2
acceptance bullet "A1 extracts fx_rate_move with zero Python edits" is
now MET; Phase 2 has no disclosed gaps left.

## M13 -- Phase 4 begun: the A2 consistency fix, a real bug found and closed

First slice of `docs/generalization_plan.md` Phase 4 -- deliberately
scoped to the smallest, highest-value piece rather than the whole
phase's feature layer/eval harness/prompt versioning at once.

`agents/investigator_agent.py` (A2) previously validated a proposed
category only against a fixed option list, banned terms, and non-empty
reasoning -- nothing checked whether the proposal was actually
SUPPORTED by the client's own evidence the tools returned. A live run
this session surfaced the exact bug the plan had anticipated: for
PRTY00001 (EUR-only transaction history), the model proposed
`HEDGING_NEED` with reasoning stating *"the client's transactions do
not show any non-EUR activity, indicating a need for hedging"* --
self-contradicting; absence of FX exposure is `TREASURY_OPPORTUNITY`
evidence, not `HEDGING_NEED` evidence.

**Fixed with a rule, not a prompt tweak.** `config/domains_fdm.yaml`'s
`category_options` for `fixed_rate_expiry` is now a mapping naming a
`requires_evidence` predicate per category
(`TREASURY_OPPORTUNITY: balance_rising`,
`HEDGING_NEED: non_eur_currency_activity`), read via
`datainsights/domain_registry.py`'s new
`category_evidence_requirements()`. `investigator_agent.py`'s three
tool bodies (`get_client_context`, `get_currency_exposure`,
`get_recent_balance_trend`) were extracted into plain `_get_*`
functions so `investigate()` recomputes the SAME facts directly in
Python (`EVIDENCE_PREDICATES`) -- never trusting the model's paraphrase
of what it saw. `_validate()` rejects any proposal whose predicate is
false, degrading to `needs_review` with the specific missing evidence
named in `could_not_determine`.

**Verified against the real bug, not a synthetic one.** Re-ran the live
test **3 times after the fix**: the model proposed `HEDGING_NEED` for
PRTY00001 every single time (a real, repeatable bias in this local
model against this client's data -- not a one-off), and the new rule
correctly caught and rejected it every time, citing
`non_eur_currency_activity` as the missing evidence. 7 new
deterministic tests in `tests/test_investigator_agent.py` cover the
predicate logic directly (no Ollama needed): both directions for both
categories, membership-check-still-runs-first, an unconstrained
category with no `requires_evidence` entry, and a check that every
predicate name the YAML references actually exists in
`EVIDENCE_PREDICATES` (so a typo fails loudly, not silently).

Full suite: **366 passed** (was 359). `investigate()` isn't wired into
any demo script or the dashboard yet (A3 confirmation flow remains
unbuilt, per earlier disclosure), so this fix has no demo-script
regression surface to re-verify -- confirmed by grepping for
`investigate(` callers outside the module and its own tests.

## M14 -- A third schema, this one with REAL commercial data

The user explicitly ruled out Berka (real, but retail/personal banking
data -- wrong fit for a platform whose whole point is being schema- and
sector-agnostic across commercial clients) and asked for real commercial
data instead. Researched live (web search + fetch, not guessed): the
U.S. Small Business Administration's Paycheck Protection Program
loan-level release is the strongest real, public, entity-level
commercial candidate -- real borrower names, real NAICS industry sector
codes, real loan amounts/dates/outcomes, U.S. government open data with
no licence ambiguity. No public source anywhere discloses real
transaction/balance history for a real commercial client (a
confidentiality constraint on the entire data category), so this is
necessarily a hybrid: real entities/sectors/loans, synthetic deposit
activity layered on top -- disclosed in every output, exactly the same
discipline `data_generator/fdm/generate_fdm.py` already holds itself to.

**Built and verified, not just described:**
- `data_generator/external/fetch_sba.py` -- downloaded the real
  `public_150k_plus` PPP loan file (968,524 loan rows, 452,077,279 bytes,
  verified byte-exact against the source's reported `Content-Length`),
  gitignored, with a `PROVENANCE.md` matching `fetch_berka.py`'s
  disclosure discipline.
- `data_generator/fdm/load_sba.py` -- samples 400 real loans (seed 42,
  filtered to `Paid in Full`/`Charged Off` status, non-null NAICS/name/
  date), spanning **24 distinct real NAICS sectors** (construction,
  healthcare, professional services, manufacturing, retail, hospitality,
  and more -- verified against the actual sampled data, not assumed);
  generates one synthetic deposit account per real entity using the same
  techniques `generate_fdm.py` already uses (random-walk balances, a
  cash-buildup subset, a dormant subset, a step-change subset), scaled
  off each entity's real loan size.
- `config/entities_sba.yaml` + `config/bindings/sba.yaml` -- one physical
  `agreements` table carries both the REAL loan rows
  (`product_type_cd=PPP_LOAN`) and the SYNTHETIC deposit rows
  (`product_type_cd=DEP`), the same one-table-multiple-product-classes
  pattern `fdm.yaml`'s binding already uses. `RiskGradeVersion`/
  `PartyMetricVersion`/`CollateralValuation` honestly marked unavailable
  (PPP loans carry only a one-time terminal status, not a rating history;
  they're federally guaranteed, not collateralised).
- **One real, disclosed gap this surfaced in Phase 0's own runtime
  factory**: `FdmLocalSource` (the only reader `offline_local` mapped to)
  unconditionally expects bi-temporal `EFFECTIVE_START_DT`/
  `EFFECTIVE_END_DT` columns -- a flat contract like this one (or
  `legacy`'s) doesn't have them and would have crashed. Added a new
  `offline_local_flat` backend (`datainsights/config.py`,
  `datainsights/runtime.py`) mapping to `OfflineLocalSource` instead --
  additive, the existing `offline_local`→`FdmLocalSource` mapping
  `fdm_local.yaml` depends on is untouched. New
  `config/profiles/sba_local.yaml` uses it.
- `tests/test_sba_binding_end_to_end.py` (6 tests): binding validates
  clean; a real-sector-diversity assertion (≥10 distinct NAICS codes,
  checked against the actual sampled data); deposits/lending tools run
  error-free across a 40-party sample; at least one real detection fires;
  risk tools degrade honestly. All 6 passed. Ran the full
  `evaluate_book()` pipeline (detectors → correlation → deterministic
  assembler) against 60 real SBA-sourced clients end to end: 11 real
  recommendations (6 `FINANCING_NEED`, 5 `ADVISORY_ONLY`), same code path
  as every other schema.
- Dashboard's Capabilities tab gained a fourth live-runnable proof button
  for this schema, alongside the existing three.

Full suite: **372 passed** (was 366).

## M15 -- Two real dashboard bugs fixed, Phase 4, Phase 5a's onboarding utility

User asked to proceed through Phases 4 and 5 without stopping, and
before that reported two real bugs from the Streamlit dashboard via
pasted screenshots.

**Two dashboard bugs, fixed and verified:**
- `streamlit run dashboard/app.py` crashed with
  `ModuleNotFoundError: No module named 'agents'`/`'datainsights'` --
  Streamlit puts the launched script's own directory on `sys.path`, not
  the repo root, so the import only worked by accident depending on
  invocation `cwd`. Fixed with an explicit `sys.path.insert(0, str(ROOT))`
  guard in `dashboard/app.py`.
- The Capabilities tab crashed with a `SyntaxError` from Streamlit's
  "magic write" auto-display machinery, which tries to `ast.parse()` a
  fragment of source around any bare expression statement -- three
  occurrences of `st.success(...) if ok else st.error(...)` as a bare
  ternary statement (not assigned or returned) triggered it. Fixed by
  converting all three to explicit `if/else` blocks.
Both verified via Streamlit's `AppTest` harness across all tabs and a
real relaunch with a clean startup log.

**Phase 4 -- ML/GenAI industry-standard practice: DONE.** PIT feature
helper (`datainsights/features.py`, deliberately not wired into
existing detectors -- they already implement their own PIT filtering
correctly; wiring would be a rewrite, not a fix); label pipeline +
model registry (`datainsights/ml/label_pipeline.py`,
`datainsights/ml/model_registry.py`) run live against the one real
feedback row this session's own dashboard testing had produced; prompt
versioning (`datainsights/prompts.py` + `prompts/*.yaml` +
`prompts/hashes.json`, verified to actually catch drift by deliberately
editing a template and watching the pinned-hash test fail); an eval
harness with three golden sets (A1 event extraction, A2 investigator
evidence-consistency, A3 RM Q&A -- A3 newly built) that ran live against
the real local Ollama models; a guardrail/red-team sweep
(`tests/test_guardrails.py`, 22 tests) including a static source-code
check that `protected_evaluator_only`/`trigger_events` are referenced
only in disclosed comments, never read. Full detail and honest
scoping in `docs/generalization_plan.md`'s Phase 4 section. Full suite
after Phase 4: **419 passed** (was 372).

**Phase 5a -- self-service schema onboarding: DONE for CSV input; 5b/5c/5d
not started.** New `onboarding/` package (profiler → LLM binding
proposer, validate-or-reject discipline like every other agent in this
repo → human-gated `accept()`, the only thing that writes to `config/`)
plus a new dashboard "Onboard" tab. Proven with a genuine blind test
against real, external SBA data the model had never seen shaped this
way: the two unambiguous concepts came back exactly right, and the two
genuinely ambiguous ones came back wrong with unwarranted apparent
confidence -- caught by the mandatory human-review step, not the model
itself. That's not a caveat to explain away; it's the real
demonstration of why this phase requires a human gate. Verified
additive-only both by a dedicated test
(`test_onboarding_never_touches_real_config_directory`) and by
inspection: zero changes to any existing detector, agent, correlation
module, or `DataSource`. Full, honest detail -- including exactly which
of the plan's own acceptance criteria were met, not met, or met by a
harder substitute -- in `docs/generalization_plan.md`'s Phase 5 section.

Full suite: **434 passed** (was 419) in 243s, plus one separately-run
live onboarding test (`tests/test_onboarding_live.py`, excluded from
the count above by the repo's existing `-k "not live"` convention)
passing in 180s against real SBA data.

## M16 -- Principal-engineer review: measured scale ceiling + 3 real bugs fixed

A review asked whether this scales to millions of records. Answered by
MEASURING the real whole-book path, not by reading it.

**The finding: cost grew as O(clients x table_size).**

| Dataset | Clients | read_entity calls | Rows materialised | Wall | ms/client |
|---|---|---|---|---|---|
| 22k rows | 60 | 2,032 | 2,439,740 | 11.4s | 190 |
| 182k rows | 60 | 2,004 | 19,540,890 | 26.9s | 448 |
| 182k rows | 300 | 10,024 | **98,063,596** | 139.4s | 465 |

ms/client RISES with dataset size (190 -> 465) -- the quadratic
signature. Extrapolated to a 50k-client book over a 5M-row balance
table this is ~10^12 rows materialised. It does not run.

**Root cause, and the fix that was already built but never plugged in.**
`datainsights/sources/caching.py`'s `RunScopedCache` has existed and been
tested since M8; `grep` showed it referenced ONLY from its own test --
never from `datainsights/runtime.py` or any run path. Now wired into
`build_runtime()` (default on, `cache=False` escape hatch for a
long-lived process sweeping multiple as-of dates):

| 300 clients / 182k rows | calls | rows | wall | ms/client |
|---|---|---|---|---|
| Before | 10,024 | 98,063,596 | 139.4s | 465 |
| After | **10** | **181,848** | **18.9s** | **63** |

7.4x faster, 539x fewer rows, and ms/client goes FLAT with book size.

**Disclosed honestly: caching is not the whole fix.** With the cache on,
`CanonicalSource` still re-merges and re-projects the full table per
client -- measured at 860 `read()` calls re-assembling 2,373,916 rows for
60 clients on a 22k-row dataset. That residual O(clients x rows) needs
the loop inverted, not cached. Feasibility measured: the detectors are
ALREADY set-based (`cash_buildup.detect` groups by PRTY_ID/AGRMNT_ID) and
`CanonicalSource.read()` already accepts no party_id, so one whole-book
pass ran in 0.12s vs 1.80s for the per-client loop over 300 clients.
Equivalence held across 5 windows on 2 datasets -- but all were
ZERO-detection windows, so positive-case equivalence is still unproven
and the set-based path was NOT adopted on that evidence. Tracked.

**Bug 1 -- only the first deposit account was ever examined.**
`agents/tools.py` did `dep.iloc[0]["AGRMNT_ID"]` in all three deposit
tools. FDM synthetic data has max 1 deposit account per client, so no
test could see it; the legacy dataset has **251 of 300 clients with >1
account** (363 current + 77 savings) and SBA has 2 per party. A
commercial client with a current AND a savings account had one silently
ignored. All three tools now scan every deposit account (the lending
tools already did this correctly). Regression-tested in
`tests/test_multi_account_clients.py`, which stubs the canonical source
to build the shape the shipped data CANNOT produce -- two deposit
accounts where only the SECOND one has the buildup, with the flat
account deliberately first, so a tool still reading `iloc[0]` returns
not_detected and fails. Plus a control (buildup on the first account
still found) and a negative case (two accounts must not manufacture a
detection).

**Bug 2 -- TREASURY_OPPORTUNITY had never fired, on any dataset, ever.**
`fdm_rm_worklist.csv` was FINANCING_NEED 15 / ADVISORY_ONLY 6 /
RISK_REVIEW 3, and both recorded scale manifests showed no
TREASURY_OPPORTUNITY at all. Cause, measured rather than guessed:
`min_increase_pct: 0.15` is compared as a FRACTION (15%), and the
generator's buildup cohort topped out at a **0.1487** rise over the
detector's 60-day window on the dev set and **0.144** on the scaled set
-- short by 0.13pp and 0.62pp. Zero accounts could ever cross, on either
dataset. The detector was correct; the generated data could not reach it.
Fixed in the generator (bounded 2%/week ramp anchored to END_DATE-400d,
capped at 3x opening balance) using a fixed window and a plain
assignment so NO extra rng draw is consumed -- every other entity
regenerates byte-identically, confirmed by the event generator still
selecting PRTY00036/PRTY00037. Result: max rise 0.296, 15 of 60 accounts
cross, 177 detections, and **12 TREASURY_OPPORTUNITY rows now reach the
RM worklist** (36 rows total, up from 24).

**Bug 3 (found BY the new test) -- two more detectors are dark.**
`check_facility_utilization` and `check_fixed_rate_expiry` produce zero
detections at the demo as-of. Shared root cause: the generator anchors
those cohorts to END_DATE (2026-06-30) -- utilisation spikes only in the
final 30 days, fixed-rate ends cluster in the final 89 -- but every demo
and test evaluates at `as_of = event_date + 90 = 2025-10-04`, ~9 months
earlier. The cohorts exist and the detectors are correct; the interesting
events sit in the future relative to the as-of actually evaluated.
Deliberately NOT fixed in the same change as cash_buildup -- re-anchoring
three cohorts at once would make it impossible to attribute which change
produced which detection. Documented in `DARK_BY_DESIGN`.

**The systemic fix: `tests/test_detector_coverage.py`.** Runs all 9
registered detector tools across the whole shipped book and asserts the
set of never-firing detectors EXACTLY equals a documented allowlist --
exact equality in both directions, so a detector that starts firing must
be removed from the allowlist and one that stops firing fails the build.
Unit tests structurally cannot catch this class of bug: they build their
own fixtures that DO cross the threshold.

**A gotcha worth recording.** The file was first named
`test_detector_liveness.py` and silently never ran -- "liveness" contains
"live", so the repo-wide `-k "not live"` convention deselected it (434
passed / 13 deselected instead of 436 / 11). A guard against silent
regressions was itself silently skipped. Renamed; the reason is recorded
in the file's own docstring.

Full suite: **439 passed, 11 deselected** (was 434/11 before this work:
+2 detector-coverage, +3 multi-account regression). Dashboard re-verified
across all 9 tabs via `AppTest`.

Plan for the ML/AWS/Snowflake questions this review raised:
`docs/ml_strategy_plan.md`.

## M17 -- The ML strategy plan (docs/ml_strategy_plan.md), all 9 tasks executed

Following M16's principal-engineer review (which ended at 439 passed),
executed the full plan the review produced: self-service ML tooling
(T1-T5), the scale fix's data layer (T6), the P0 entitlement gap (T7),
cloud contracts (T8), and documentation (T9). Every claim below is backed
by a real run recorded in this session, not an intention -- see
`docs/ml_strategy_plan.md`'s own per-task "Verified:" notes for the detail
behind each line.

**T1 -- ML-eligibility profiler** (`onboarding/ml_profiler.py`).
Deterministic: numeric + has a time column + >=8 observations/entity +
>=30 entities + <=20% null + non-constant. Run against real FDM data:
`agreement_daily_balance.AGRMNT_LDGR_BAL_AMT` (the actual E2 measure)
eligible; `party.RSK_GRD_VAL` correctly rejected (1.2 obs/entity);
every identifier column never even assessed. 6 tests.

**T2 -- `config/ml_policy.yaml` + loader** (`datainsights/ml/policy.py`).
Precedence enforced: explicit human policy > Gate1+binding (auto-enabled,
no config needed) > accepted LLM proposal > none. Unknown algorithm
raises at LOAD time naming the valid set; an unexpected YAML field is
rejected (`extra="forbid"`), not silently dropped. 8 tests.

**T3 -- LLM measure proposer** (`onboarding/ml_measure_proposer.py`).
Mirrors `binding_proposer.py`'s discipline exactly: never proposes a
column that failed Gate 1, never re-proposes an already-mapped one, any
model failure becomes a rejected proposal not a crash. Live-tested
against real Ollama: correctly recognised the real E2 measure as
relevant with stated confidence. 4 tests (3 deterministic + 1 live).

**T4 -- Champion/challenger runner** (`datainsights/ml/runner.py`).
`python -m datainsights.ml.runner --profile fdm_local` -- real run: 60
agreements, 3 disagree, deterministic detected 15 / isolation_forest
detected 12, manifest written with the disagreement-is-not-improvement
caveat inline every time. `Transaction.amount` correctly reported
SKIPPED (policy resolves it to deterministic) rather than faking a
comparison. Explicitly scoped to the `fdm` schema today -- the two known
E2 comparisons call FDM-specific `DataSource` methods; legacy/sba report
"not wired" honestly. 5 tests.

**T5 -- Dashboard "ML opportunities" tab.** Three steps matching T1-T4:
scan → decide (checkbox + algorithm dropdown, changes nothing until
**Save**) → run comparison, plus a model registry viewer. Verified via
`AppTest` across all 10 tabs, then interactively: clicked Scan, switched
schema fdm->sba, toggled a policy checkbox, clicked Save -- all
exception-free. One real finding from that interactive test: the Save
button writes the actual `config/ml_policy.yaml` (by design -- it's the
real config file, not a demo copy), so exercising it live during this
session's own verification mutated repo state; the test-added `sba`
entry was caught and reverted before this session ended. A future
automated dashboard test of this button should use a tmp path.

**T6 -- Aggregate pushdown** (`datainsights/sources/offline_local.py::aggregate()`,
`datainsights/features.py::compute_many()`). Real DuckDB SQL pushdown for
`OfflineLocalSource` (the `legacy`/`sba` backend), golden-tested against
the pandas fallback: agree to < 1e-6 across mean/sum/count/last/first on
real SBA balance data (400 accounts), including a test where a source
lies about supporting pushdown but has no `aggregate()` method (must
fall back cleanly, not crash). Scope cut, disclosed: `FdmLocalSource`'s
bi-temporal as-at collapse is NOT covered -- replicating that correctly
in SQL is real, separate work. 8 tests.

**T7 -- RM entitlement** (the P0 gap M16 flagged as blocking any bank
pilot). `Party.relationship_manager_id` added to the semantic model,
`fdm.yaml`'s binding, `config/entities_fdm.yaml`'s contract, and the
generator (`RM{sha256(prty_id) mod 20}` -- a plain hash, zero rng draws,
so it can never perturb any other field's random stream). Regenerated
all 3 FDM datasets and confirmed: only `party.csv` gained the new
column, and the event generator still selected the same PRTY00036/37
pair it always has -- the RNG stream stayed aligned. Live run: 36-row
worklist, 16 distinct RMs represented, 0 empty ids. `legacy`'s binding
confirmed to degrade honestly (column simply absent, no crash) -- and
its own disclosure comment now flags that `clients.csv` genuinely HAS
this field, just isn't contracted as an entity yet (a concrete, scoped
next step, not invented data). 4 tests.

**T8 -- AWS/Snowflake ML backend contracts** (`datainsights/ml/backends/`).
`sagemaker_training.py`, `snowpark_training.py` -- both construct with
zero network calls, both `train()` calls raise `NotImplementedError`
naming the exact blocker (no credentials + no CLAUDE.md authorization).
Snowpark's message explicitly does NOT treat "it's not Cortex" as
authorization -- it states that distinction and still requires separate
sign-off. 4 tests.

**T9 -- Documentation.** `docs/ml_quickstart.md` (new -- the baby-steps
guide, every CLI snippet in it actually run and verified this session,
not just written); `onboarding/README.md` (new -- the single onboarding
page that didn't exist before, covering both schema onboarding and ML
opportunity identification); `docs/PROJECT_CONTEXT.md` (new §8c, the
portable summary of the whole ML design); `docs/gap_analysis.md` (RM
entitlement and self-service-ML rows closed, 2 new rows added for the
scope actually cut: `fdm`-only comparison coverage, `OfflineLocalSource`-
only pushdown); this M17 section.

Full suite: **477 passed, 12 deselected** (was 439 passed, 11 deselected
after M16 -- +38 new tests across T1/T2/T3/T4/T6/T7/T8, +1 newly
deselected live test for T3), plus the T3 live test passing separately
against real Ollama. Dashboard re-verified across all 10 tabs (the new
"ML opportunities" tab included) via `AppTest`, plus live interactive
verification of every button on the new tab.

## M18 -- Reviewing M17 against its own plan: two controls that did nothing

Reviewed the M17 implementation against `docs/ml_strategy_plan.md` as
written, rather than restating it. Found two defects of the same
class -- a control that LOOKS like it works but doesn't -- plus a
reproducibility trap. All three are now fixed and regression-tested.

**Defect 1 (serious): the ML Enabled toggle was a silent no-op.**
`datainsights/ml/runner.py` only skipped a measure when
`algorithm == "deterministic"`. It never checked `enabled`. So a measure
set to `enabled: false` with `algorithm: isolation_forest` -- the exact
state produced by unticking the dashboard checkbox without also changing
the algorithm -- **still ran the full comparison**. The primary
user-facing control, and the one `docs/ml_quickstart.md` tells users to
rely on, did nothing in that combination. A control that silently does
nothing is worse than no control: the user believes they have acted.
Fixed; `run()` now passes `enabled` through and `run_measure()` refuses
first, with "disabled in policy" as the stated reason (it beats the
algorithm reason, so the UI explains the cause the user actually acted
on). Regression-tested in `tests/test_ml_controls_are_real.py`, including
an end-to-end test that a policy FILE disabling a measure reaches the
runner.

**Defect 2: T7's entitlement field was invisible in the UI.** M17 added
`relationship_manager_id` to the FDM worklist and called it the fix for
"blocks any bank pilot" -- but the FDM worklist renderer showed neither
the column nor a filter, so no RM could ever use it. T7's own acceptance
criterion ("dashboard can filter by it") was never met; the three
matches a naive grep found were in the LEGACY renderer, which had an RM
column long before this work. Fixed: **My RM code** is now the first
filter in the FDM worklist (it is the first question an RM asks), the RM
column is shown, and filtering prints the in-view count and indicative
revenue. Verified live: 16 RM codes, filter applies correctly.

**Reproducibility trap found while verifying that fix:**
`var/insights/fdm_rm_worklist.csv` still lacked the new column, because
M17 only ever built the worklist in memory. Anyone following the docs
would have seen no RM feature at all until they re-ran the pipeline.
Artifact regenerated, and `docs/user_guide.md` §6d now warns explicitly
that the dashboard shows the last-written artifact, not live state.

**Doc corrections (the user-facing ones were the worst):**
`docs/user_guide.md` -- the most product-shaped doc -- listed dashboard
tabs that no longer exist ("Run the pipeline", "Worklist" were merged
into "Explore a source" tabs ago), claimed a "16 cases" test suite
against an actual 481, and documented only the legacy digest as the
output, never the RM worklist that is the real product. All three fixed,
plus a new §5b "What a Relationship Manager actually gets" that reads a
worklist row in the documented category → hypothesis → sized-action
order. `README.md`'s "Start here" table now routes to the RM view, the
ML quickstart and onboarding, instead of only to engineer docs.

**Honestly disclosed, not fixed:** the ML tab's Step 2 list is narrower
than Step 1's scan (Step 1 says what COULD support a challenger; Step 2
only offers what also has a comparison wired in `MEASURE_COMPARISONS`).
Rather than pretend otherwise, Step 2 now states this and lists
eligible-but-unwired measures with the exact extension point. Also still
open: `require_challenger_win` is declared in `config/ml_policy.yaml` and
in `policy.py` but consumed nowhere -- a governance claim not yet
enforced; and `chosen_by` provenance does not yet reach a model card as
§3 of the plan requires (latent -- no model exists to card yet).

Full suite: **481 passed, 12 deselected** (was 477; +4 regression tests).
All 10 dashboard tabs re-verified via `AppTest`, plus live interaction
with the RM filter and the ML scan.

## M19 -- Dashboard information architecture: one tab, one job

User feedback, in their words: "the flow is not very clear", "where can I
see the data?", "is it just a single table?", and -- looking at the live
narration page -- "can this page be made more descriptive". Each turned
out to point at a real defect, not a preference.

**Two tabs both claimed "data", so neither owned it.** "Data sources"
previewed a HARDCODED list of five legacy CSVs against `DATA_DIR`. FDM
and SBA data were viewable NOWHERE in the UI -- which is why selecting
the SBA source showed nothing but a proof button. Restructured by
job-to-be-done rather than by merging everything into one page:

- **Explore a source** now owns all data viewing. A new per-source
  browser (`_render_source_data_panel`) lists every table with row and
  column counts, previews any one of them, and then maps them onward:
  physical tables -> canonical concepts (with availability and the
  reason when a concept is genuinely absent), then signals -> categories.
  Verified live: SBA **4** tables, FDM **11** (across `kernel/` and
  `lending/`), legacy **8**. The answer to "is it just a single table"
  is no, for every source -- and the expander header now says so before
  you even open it.
- **Onboard a source** is now its own top-level tab. Burying the only
  screen in the app that writes to `config/` inside an exploration tab
  was poor UX; it also pairs cleanly now (explore = read, onboard = add).
- **Local vs. Snowflake** moved to **AWS target architecture**, where a
  deployment question belongs.
- **Exogenous events** sit alongside the per-source browser, explicitly
  labelled as shared across sources -- because they are: the same feed is
  checked against whichever book is loaded.
- **Data sources** deleted. Still 10 tabs, each with one job.

**A leakage boundary that only existed at the wrong layer.**
`data_generator/output/protected_evaluator_only/trigger_events.csv` sits
INSIDE the legacy source's own directory. `OfflineLocalSource` refuses
protected paths -- but the new browser reads CSVs directly with pandas
and would have walked straight past that guard, listing and previewing
evaluator-only ground truth in the UI. Hard-excluded in the browser and
locked in by `tests/test_dashboard_never_lists_protected_data.py`, which
is deliberately NON-vacuous: it first asserts the protected file exists,
so it can never pass merely because the fixture went missing.

**"Where do the agents collaborate?" was invisible, not missing.**
`ClientEvaluation` already carried every piece -- per-domain
`agent_results`, the `signals` bus, `exogenous_confirmed`, the final
`recommendation` -- but nothing rendered it. New **Trace one client**
tab (now the FIRST sub-tab) shows five stages: agents running
independently -> the signal bus -> the exogenous qualification ->
the arbitration -> the RM card. Stage 4 exposes what was completely
hidden: the strongest signal wins the category, a RISK_REVIEW signal or
the high-risk flag SUPPRESSES a revenue category, and confidence is
shown decomposed (base + domain bonus + exogenous bonus) rather than as
one opaque number.

**And you never needed to run the whole book first.** You never did --
but the per-client view was a sub-tab UNDER the worklist, captioned "the
whole-book tab above is the volume view", which implied an order that
did not exist. The three sub-tabs now state plainly that each runs
independently.

**A lineage panel answers "where does each agent get its data".** Per
agent: canonical concept -> the physical table(s) it resolves to under
the active binding, including join counts (`Account` ->
`AGREEMENT + PARTY_AGREEMENT + MORTGAGE_AGREEMENT`), plus the tables
that actually produced a signal for this client. This also answers the
multi-table/multi-database question honestly: **several tables per
concept, yes, proven; several databases in one run, no** --
`CanonicalSource` takes exactly one source, a profile declares exactly
one `source:` block, and there is no federation code anywhere.

**Live narration is no longer a stdout dump.** It now runs the same
`evaluate_client()` in-process with `narrate=True` and renders one card
per agent (observed facts / hypothesis / suggested action / caveats),
each labelled with whether the LLM narrated it or it fell back to a
template, plus latency and prompt version. Verified with a real Ollama
run.

**Found while building the category map:** of the six categories,
**only four are reachable from any detector**. `HEDGING_NEED` and
`CAPEX_FINANCING` have labels and colours but no signal mapped to them,
so they can never appear on a worklist. The UI now warns about this
rather than implying all six are live -- same class of bug as M16's dark
`TREASURY_OPPORTUNITY`, caught before it misled anyone.

Full suite: **483 passed, 12 deselected** (was 481; +2 for the protected-
path regression test). All 10 tabs verified via `AppTest`, plus live
interaction with the data browser, the trace, the RM filter and a real
narration run.

## M20 -- Architect's review: how generic this really is, and the plan

Asked, in order: is the implementation going the right way; why is it so
hard to add a category; is there hardcoding and is the code reusable;
what triggers detection; and what is the production design for the
agents. Answered with evidence rather than opinion -- every finding is a
file:line grep or read -- in two documents:

- **`docs/hardcoding_audit.md`** -- the review. Verdict: the foundations
  (source ABC, semantic layer, correlation, propose/accept discipline)
  are right and should be left alone; execution drifted one level too
  concrete. Three structural findings: the 9 detectors speak FDM physical
  vocabulary and genericity is achieved by six rename maps in
  `agents/tools.py` (generic by shim, not by design); eight
  business-content dicts live in Python, not config; the semantic model
  is a fixed constant duplicated in code. Four modules bypass the
  canonical layer with FDM-only method calls, which is why whole-book
  runs work for `fdm` alone -- and the guard test that should catch it
  scans five files for five table names. The domain "agents" are handed
  `tools=[]` and only narrate pre-computed facts; the one real reasoning
  agent (A2) has zero callers. Cross-domain linking is counting, not
  reasoning. SLOT E2 is per-entity robust statistics on <=30 points, not
  ML, and its eligibility floors were tuned to pass the demo data.

- **`docs/refactor_plan.md`** -- the executable plan. Three questions
  answered first: there is NO trigger (detection is pull-based batch at a
  hand-chosen `as_of`; the `monitor:` block nothing reads); a category is
  a string literal ~8 places agree on by convention, two of six reachable
  from no detector; the detectors are NOT coupled to categories (every
  mention is a docstring). Then the RM journey today (feedback captured,
  2 labels, loop open, nothing learns) vs target (learning enters at Tier
  3 only, re-ranks, never adds or recategorises). Then R1-R12 in four
  waves with interface + acceptance each: restore the foundation (R1
  canonical detectors, R2 content->config + categories registry-driven,
  R3 close bypasses + tighten guard) -> make reasoning real (R10
  cross-domain rules, R9 wire the investigator, R7 principled ML gate)
  -> become a platform (R4 semantic registry + domain packs, R6
  per-binding rules, R8 profile proposer) -> listen (R11 watermarked
  incremental runs, R12 triggered exogenous ingestion, R5 real sources).

A whole-repo sweep then added §6 to the plan -- eleven more findings
and ten more tasks (R13-R23), several outranking the original list:
the O(clients x rows) scale ceiling had no task; one client's
exception aborts a whole book; no CI or lint exists; `.env` was not
gitignored (fixed); five config fields declare governance nothing
enforces; two full pipelines with no decision on the second; EUR
hardcoded 16 times; no run-overlap guard; no model-change procedure;
no outcome-based evaluation; entitlement is a dropdown; two monoliths.
Each carries a decision. Twenty-three tasks in five waves.

Nothing in either document is built. Both are linked from `README.md`'s
"Start here" table and `docs/PROJECT_CONTEXT.md`'s file map.

## M21 -- Executing docs/refactor_plan.md: Wave 0 and most of Wave 1 landed

Every claim below is a real run on 2026-09-19; the suite went 483 ->
648 passed (the new guards are parametrized per file/field) with zero
regressions, and the whole-book worklist is byte-identical before and
after the largest change.

**Wave 0 -- R16, protect.** `ruff` gate (pyflakes + syntax errors; the
codebase had 39 findings, 38 auto-fixed, 1 by hand), `.github/workflows/ci.yml`
(regenerates every dataset it needs, deselects live-LLM tests, skips SBA
which is a 450 MB download), `.pre-commit-config.yaml`,
`requirements-dev.txt`. `.env` was NOT gitignored -- fixed. Nothing ran
the 483 tests automatically before this.

**R1 -- detectors go canonical.** All 9 `REQUIRED_COLUMNS` now use
`config/semantic_model.yaml` names (`balance`, `observed_at`,
`account_id`, `direction=credit|debit`, ...). The six canonical->FDM
rename maps in `agents/tools.py` and the inline back-translation in
`external_events/exposure_checks.py` are deleted -- genericity is now by
design, not by shim. `grep` for any FDM input token across
detection_engine/, agents/, external_events/ and the ML comparison
modules returns nothing. Detector OUTPUT keys (`prty_id`, `agrmnt_id`,
`orig_limit` -- the contract sizing consumes) deliberately untouched.
**Equivalence proven on real data:** the 36-row FDM worklist is
identical before and after. Nine detector-fixture test files renamed;
the physical-layer tests (source, generator contract, profiler) were
correctly left alone.

**R3 -- the bypasses are closed and the guard can see now.**
`demo_fdm_scenario.py`, `scale_evaluation.py`, `compare_baselines.py`,
`evaluate_baselines.py` all read through `CanonicalSource`; no
`FdmLocalSource`-only method is called anywhere in the pipeline. Direct
consequence: the ML champion/challenger comparison now runs on
`sba_local` (400 accounts) -- structurally impossible before.
`product_codes` (declared, documented as read, called by nothing) is
deleted from the registry, the YAML and `docs/adding_a_new_domain.md`.
`tests/test_no_source_specific_coupling.py` rewritten: it scanned 5
files for 5 table names and passed while all of the above existed; it
now scans every pipeline package for every column in every
`entities_*.yaml` and every FDM-only method, subtracting each detector's
own `DETECTION_COLUMNS` dynamically (its first catch was a false
positive: `status`/`orig_limit` are detector OUTPUT fields that collide
by name with two SBA physical columns -- code reading its own output is
not coupling). The two legacy detectors and the composition root are
excluded with the task (R23) or reason attached, never silently.

**R14 -- config that promised what code didn't deliver.**
`tests/test_config_fields_are_enforced.py` introspects every pydantic
field of `Profile` and the ML policy, plus every entity-contract key, and
requires each to be read outside its schema, constrained by a `Literal`
or `Field`, referenced in a validator, or listed in a register with the
task that will enforce it -- checked in both directions so the register
cannot grow silently. It found MORE than the five fields the audit
named: `config_version` (a version nothing compared -- now `Literal[1]`),
`source.cost_policy` (a free string nothing read -- now a closed set with
a consistency validator: a cloud backend may not claim
`no_cost_local_files`, a local one must), `llm.fallback` (every profile
said `deterministic_template`, nothing read it -- now a one-value Literal,
so the only fallback CLAUDE.md permits is the only one configurable),
plus `monitor.lookback_ref` and three Snowflake/Athena tuning fields,
registered under R11/R5. Three false positives in the test's own
heuristics were fixed on the way rather than papered over (a Literal IS
enforcement; a field name in a comment is not a reader; a leaf declared
by two different models is two declarations, not a validator).

**R15 -- one bad client no longer aborts the book.** `evaluate_book()`
was a list comprehension; it now isolates per client, records the
failure on `ClientEvaluation.error`, and continues. Regression-tested.

**Also found and fixed:** `tests/test_domain_registry.py` only passed
when another test had imported `agents.tools` first -- order-dependent;
now self-sufficient.

Remaining in Wave 1: R2 (business content -> config; categories
registry-driven) and R13 (set-based whole-book evaluation).

## M22 -- R2 landed: categories are config, not convention

Real run, 2026-09-19. Suite 648 -> 659 passed (8 R2 + 3 R13 acceptance tests),
ruff clean, and the 36-row FDM worklist is byte-identical before/after
-- the change moves content, it does not change a single recommendation.

**What moved.** `config/categories.yaml` (new; label, description,
colour, `revenue_model`, `revenue_mechanism`, `talking_point`,
`suppressed_action`) read by `datainsights/category_registry.py` --
self-loading and lru-cached exactly like `domain_registry.py`. Each
signal's `why_now` and `non_revenue_action` now sit in its own block in
`config/domains_fdm.yaml`, via `domain_registry.why_now_for()` /
`non_revenue_action_for()`. The legacy macro-event mapping
(`MACRO_HYPOTHESIS` + `categorize_macro_event`) is
`config/legacy_macro_hypotheses.yaml` -- see the plan for why not
`event_types.yaml`.

**What is gone.** Eight Python dicts: `REVENUE_CATEGORIES`,
`NON_REVENUE_ACTION`, `SUPPRESSED_ACTION` (hypothesis.py);
`REVENUE_MECHANISM`, `TALKING_POINT`, `WHY_NOW` (fdm_worklist.py);
`MACRO_HYPOTHESIS` (worklist.py); the `CATEGORY_INFO` literal
(dashboard). Plus `rules.yaml`'s `no_revenue_categories` -- a second
source of truth that could disagree with the first; now derived from
`revenue_model: none`. `indicative_revenue_eur` dispatches on
`revenue_model`, not on category-name branches. `grep` for any of the
eight names outside docs returns nothing.

**Proven by `tests/test_category_registry.py`.** A fixture YAML adds
`SUPPLY_CHAIN_FINANCE` (financing) and `ESG_ADVISORY` (none) and maps
signals to them: `assemble()` returns them, the high-risk flag suppresses
the revenue one (rule 1 asks the registry, not a literal set), the none
one gets no offer and its own YAML action text, `HEDGING_NEED` becomes
reachable with one YAML signal mapping, the dashboard card is derived
(no literal category keys in `dashboard/app.py`), every `category:`
referenced by any config file is declared, and an unknown category
raises rather than defaulting. Zero code edits in any of those paths.

**Not changed, on purpose.** `TRANSACTION_RULE_CATEGORY` and
`REVENUE_PCT_HEURISTIC` in the legacy `worklist.py` -- they die with the
pipeline in R23; moving them first would be work thrown away.
`docs/adding_a_new_domain.md` §6b is the user-facing recipe.

**R13 -- the whole-book path stops being O(clients x rows).** Root
cause, from the code: `evaluate_client` built a fresh `CanonicalSource`
per client, and every tool's `canonical.read(..., party_id=)` re-assembled,
re-projected and boolean-masked the FULL entity. Fix, in
`datainsights/semantic/canonical.py`: the projected, as-at-collapsed
frame is cached per (concept, as_at) on the instance, and a
`party_id`/`account_id` filter is a `groupby(...).indices` position
lookup -- O(that client's rows). `evaluate_book` constructs ONE
`CanonicalSource` and passes it through a new `canonical=` kwarg on
`evaluate_client`; a single-client caller (Trace one client) still gets
a fresh one. Tool code untouched, so equivalence is by construction and
was also measured:

| set | clients | positives | per-client path | shared (R13) | ms/client |
|---|---|---|---|---|---|
| FDM dev | 60 | 36 | 2.00 s | **0.96 s** | 33 -> 16 |
| FDM scaled | 300 | 75 | 12.37 s | **4.55 s** | 41 -> 15 |

The plan's acceptance was "300 clients under 5 s" -- met, narrowly, and
ms/client is now flat with book size; what remains is the detectors
themselves. **Golden equivalence on a positive window** -- the thing M16
explicitly could not claim -- is `tests/test_set_based_book_evaluation.py`:
every recommendation field and every signal identical between the shared
and per-client paths on the demo window (36 positives), and the 36-row
worklist is byte-identical before R2 and after R13. The same test file
caught a real bug in the first cut: an unfiltered `read()` handed back
the cached frame object, so a tool's column assignment would have
landed in the cache -- copy-on-write protects derived frames, not the
same object. Fixed with a shallow copy on the way out; the test pins it.

One test had to change its premise, not its answer:
`tests/test_run_scoped_cache.py` asserted the source-level cache cuts
book-path reads 5x -- after R13 the shared `CanonicalSource` already
reads each entity once (15 uncached vs 10 cached reads), so the 5x claim
is now measured on the per-client path where the cache is still the only
thing collapsing reads, and the book path pins "reads < clients" instead.
Side-effect worth knowing: the full suite runs in 45 s, down from 190 s.

**Wave 1 is complete.** Next: Wave 2 -- R10 cross-domain rules table,
R9 wire the investigator, R7 ML gate, R20 model-change procedure, R22
split the monoliths.

## M23 -- Wave 2 begins: the reasoning tier becomes real (R10, R9)

Real runs, 2026-09-19. Suite green throughout; ruff clean.

**R10 -- cross-domain rules.** `config/domains_fdm.yaml` gains a
`combinations:` table (a reserved top-level key, not a domain);
`domain_registry.combination_rules()` / `matching_combination()` load it,
most-specific-first. `assemble()` consults it before strongest-signal-
wins: a matching rule sets category + hypothesis and may name
`size_from` (whose figures size the offer); rule 1 (risk suppression)
still applies after. `Recommendation.combination_rule` records which
rule fired; the Trace tab's Stage 4 shows it. `tests/test_combination_rules.py`
proves the audit's two examples -- cash building alone is treasury, cash
building with the facility drawn harder is financing, from the same
strongest signal -- plus fall-through, suppression-after-rule, most-
specific-wins, and rule validation.

**Measured, and acted on:** the audit's worked examples never co-occur in
any generated book (dev, scaled, SBA: zero firings). The combinations
that DO occur were listed from the data -- `dormancy +
facility_maturity_approaching` (3 clients, scaled), `revenue_pattern_change
+ facility_maturity_approaching` (1, dev) -- and have real banking
meaning (attrition risk before a renewal; re-size the renewal to the new
inflows), so those two rules ship alongside the examples. Effect on the
dev worklist: 1 row's hypothesis changes (category unchanged); on the
scaled book 3 recommendations move to ADVISORY_ONLY. Five rules total.

**R9 -- the investigator is wired.** `agents/orchestrator.evaluate_client`
now calls `investigate()` on a NARRATED run whose recommendation is
`ambiguous` or confirmed by more than one endogenous domain
(`needs_investigation()`); the batch path never pays for it. For a
multi-domain recommendation with no registered `category_options`, the
options are the confirming signals' own categories plus the assembled
one -- never free text. The note lands on `Recommendation.investigation`
(a dict) and `ClientEvaluation.investigation`; `nba_category` is never
touched by it. The Trace tab gains "Stage 4b -- Investigation". The dev
book has 3 Tier-2 triggers, scaled 3. `tests/test_investigator_wiring.py`
covers the trigger, the option set, a narrated run with a failing model
(honest `needs_review`, category unchanged) and that a quiet client never
calls the investigator. The live proof remains the existing live test.

## M24 -- Wave 2 complete: R7, R20, R22

Real runs, 2026-09-19. Suite green, ruff clean.

**R7 -- the ML gate is principled, and says no.** `config/ml_policy.yaml`
gains a `power_criteria:` block with two levels and the reasoning for
each number in the file: *robust_baseline* (SLOT E2's per-entity
outlier-robust statistic -- 8 obs/entity, 30 entities) and
*ml_challenger* (a cross-entity model -- 200 entities, history >= 2x the
30-point window, >= 100 RM outcome labels from `var/rm_feedback.db`).
`onboarding/ml_profiler.py` returns both verdicts per column
(`eligible` / `ml_challenger_eligible` + `ml_reasons`) and
`ml_challenger_verdict()` gives the one sentence the ML tab must say;
on today's FDM data: **no ML challenger is eligible -- 92 entities <
200; 0 outcome labels < 100**, robust-baseline comparison still fine.
E2 is relabelled a robust baseline in the tab. A real defect surfaced on
the way: the profiler counted entities on a 5000-row sample and saw 39
of FDM's 92 deposit accounts -- it now counts on full columns (only the
measure and key columns are read). `tests/test_ml_gate.py` proves the
criteria are config, the verdict on today's data names the criterion,
and the verdict flips when the criteria are met.

**R20 -- one model id.** `agents/model_factory.ModelConfig.model_id`
defaults to the active profile's `llm.model` via `default_model_id()`
(`datainsights/runtime.active_profile()` is the single resolver). The
literal is gone from 8 test files, `onboarding/propose.py`,
`datainsights/judge/run_sample.py` and a docstring;
`tests/test_model_id_single_source.py` scans every package for any
local model tag outside the profiles (the `-cloud` refusal test and the
profile fixtures are the two allowed sites). `docs/model_change_procedure.md`
is the routine: pull + pin -> golden sets -> hashes only if a prompt
changed -> guard suite -> model card.

**R22 -- the two monoliths.** `dashboard/app.py` 1,976 -> 61 lines
(sidebar + dispatch); `dashboard/common.py` holds shared helpers and
diagrams; `dashboard/tabs/<page>.py` x 10, each a `render()`; `PAGES`
in `dashboard/tabs/__init__.py`. `tests/test_dashboard_pages_render.py`
renders every page under AppTest in the suite (before: ad-hoc scripts
only) and pins the dispatcher under 120 lines. `docs/current_state.md`
1,691 -> 331 lines with an "At a glance" section; M7-M23 moved verbatim
into this file; every cross-reference to a milestone section repointed.

**Wave 2 is complete.** Next: Wave 3 -- R4 semantic registry + domain
packs, R6 per-binding rules, R17 currency, R8 profile proposer, R21
entitlement, R23 retire the legacy pipeline.

## M25 -- Wave 3 complete: the platform becomes a platform (R4, R6, R17, R8, R21, R23)

Real runs, 2026-09-19. Suite 688 -> 693 passed (21 legacy files deleted,
their tests with them; 40 new acceptance tests), ruff clean; the one
pipeline produces a worklist on all three schemas (FDM 36 rows, legacy
167, SBA 131).

**R4 -- semantic registry + domain pack.** `config/semantic_model.yaml`
is loaded as typed `ConceptSpec`s with a `kind` (entity | observation |
event | version | valuation); `onboarding/binding_proposer.py`'s Python
copy of the concept list is deleted. `config/packs/banking.yaml` +
`datainsights/packs.py` name the concepts/detectors/categories a
deployment turns on and fail the build if any is undeclared.
`onboarding/concept_proposer.py`: a profiled table that maps onto no
concept but carries Gate-1-eligible measures gets a PROPOSED concept
(`concepts.proposed.yaml`, review.md) -- validate-or-reject like every
proposer; accepting it is a human edit of the semantic model, on purpose.

**R6 -- per-binding rules.** `Binding.rules` is deep-merged over
`config/rules.yaml` in `build_runtime`; `config/bindings/sba.yaml` sets
`cash_buildup.min_prior_balance: 2500` and FDM's 5000 is untouched.

**R17 -- currency.** Every account-bearing tool reports its account's
currency; it rides on the Signal (`raw_measure.currency`) and the
`Recommendation.currency`; `_money()` formats in it; `rules.yaml` carries
`min_prior_balance_by_currency` / `min_offer_by_currency`; the worklist
has a `currency` column and the digest totals per currency; the narrator
is told the evidence's real currencies and `_currency_problem` now
rejects EUR-on-USD as well as the reverse. SBA is USD end to end
(`tests/test_currency_awareness.py`). Exogenous sizing stays EUR -- the
event value is EUR-denominated -- and says so.

**R8 -- profile proposer.** `onboarding.propose` also writes
`profile.proposed.yaml` (llm block mirrors the active profile, so the
model id keeps one source); `onboarding.accept` validates it as a
`Profile` before writing `config/profiles/<name>_local.yaml`; accept ->
run with no hand edits.

**R21 -- entitlement.** "My RM code" was a filter, not authorisation.
`Profile.identity` (provider local_dev with env overrides; `idp` is the
declared, NOT RUN Stage-3 contract), `datainsights/identity.py` resolves a
`Principal` and `scope_worklist()` applies it SERVER-SIDE before any
slicer: an RM sees only their rows (none without rm_ids -- deny by
default), a supervisor the book; a schema without RM ids shows an RM
nothing. The dropdown is gone; the sidebar shows who is signed in.

**R23 -- one pipeline.** Deleted: `datainsights/{cli,runner,ranking,
worklist,build_worklist,digest,status,state}.py`, `judge/run_sample.py`,
`detection_engine/external_macro_event.py`, `sources/{event_source,
external_event_source}.py`, `external_events/{demo_scenario,
simulate_external_events}.py`, the macro narrator trio,
`config/legacy_macro_hypotheses.yaml`, `config/profiles/offline_ollama.yaml`,
two test files, five legacy `rules.yaml` blocks, and the dashboard's
legacy renderer. Ported: `large_incoming_payment` as a canonical deposits
detector (same median+MAD statistics, per-currency floor, cooldown,
Signal + sizing + fallback sentence). It is dark on FDM (that generator
injects no outsized credit -- registered in `DARK_BY_DESIGN`) and fires
on 45 of 606 legacy clients with zero errors. Two bugs found by running
it: my first evidence dict lacked `baseline_mad` (5 of 80 clients erred
-- R15's isolation is why the book did not die), and
`agents/demo_fdm_scenario.py`'s sector/country proof crashed on a
binding without those fields. The dashboard's legacy source is now a
proof-only source of the same pipeline; Status reads `var/agent_traces.db`;
the exogenous panel reads the declarative feed. `evaluation/evaluate.py`
is kept as the only ground-truth reader until R18. Docs, README,
`run_demo.sh` and CI rewritten for one pipeline.

**Wave 3 is complete.** Next: Wave 4 -- R19 run lock, R11 incremental
runs, R12 event ingestion, R5 sources, R18 outcome backtest.

## M26 -- Wave 4 complete: the product listens (R19, R11, R12, R5, R18)

Real runs, 2026-09-19. Suite green, ruff clean. All 23 tasks of
docs/refactor_plan.md have now landed.

**R19 -- run lock, one run table.** `datainsights/runs.py`: every run
is a row in `var/runs.db` `runs` (started/finished/failed/abandoned,
what it evaluated, the high-water mark it saw); `RunLock` honours
`monitor.max_concurrent_runs` -- an overlapping run raises
`RunOverlapError`, a `running` row whose process is dead is marked
abandoned and never counts as a lock; a failed run is recorded with its
error, never left `running`.

**R11 -- incremental runs.** `run_book(profile, incremental=True)`:
the first run of a profile is full; every later run computes the
clients with any canonical row newer than the previous run's per-concept
high-water mark (`changed_parties`), adds the clients whose carried
recommendation is older than the longest detector cooldown, evaluates
only those, and carries every other client's recommendation forward
from the previous run's stored copy. Measured: second run on unchanged
data evaluates 0 of 60 clients, produces the identical worklist, in
0.14 s vs ~1 s (the plan asked for < 10 %; 14 % measured, honest). A
new balance row for one client re-evaluates exactly that client
(control-vs-treatment test). `datainsights/monitor.py` is the clock: it
finally READS the `monitor:` block (`enabled`, `interval_minutes`,
`lookback_ref` -> a rules key bounding how old a row may be and still
count as new, `max_concurrent_runs`); `--once` for a single tick. Those
four fields left the not-yet-enforced register.

**R12 -- ingestion as a triggered step.** `ingest_notices.ingest()` is
callable; `run_book` reads the extracted-events store's `extracted_at`
high-water mark and, for each NEW extracted event, enqueues exactly the
clients `qualifies()` confirms -- proven equal to the set of clients
whose first-run recommendation carried the event, i.e. not everyone in
the sector.

**R5 -- source breadth.** `datainsights/sources/sql_source.py`: one
dialect-aware `SqlSource` (duckdb, postgres, sqlserver, snowflake,
athena -- placeholder style and identifier quoting are the only
differences) implementing `read_entity` (bounded, row-limited),
`aggregate` (GROUP BY pushdown; portable window function for
last/first) and `changed_since` (R11's primitive; `supports_change_detection`
is true exactly when the contract declares an event-time field);
`AthenaSource` on top. `Profile.source` gains `postgres`/`sqlserver`
backends, `domain_schema_map`, `default_schema`; `build_runtime`
constructs the adapters lazily, so `warehouse_size`,
`auto_suspend_seconds`, `athena_workgroup`, `query_timeout_seconds`,
`statement_row_limit` now have real readers and left the register.
Proof without credentials: `tests/test_sql_source_conformance.py` loads
the SBA CSVs into DuckDB and gets frames identical to
`OfflineLocalSource` for every read and every aggregate; every real
dialect constructs without connecting and fails closed with a message
that says NOT RUN. The SBA contract now declares its event-time fields.

**R18 -- outcome backtest.** `datainsights/backtest.py`: run the
deterministic pipeline as of each T (virtual clock on the data AND on
the exogenous events -- an event dated after T does not exist at T),
join RM feedback recorded strictly after the as-of day by the stable
`recommendation_id`, report per category recommended / with outcome /
engaged / hit rate / coverage. On the shipped data it reports the one
real feedback row that exists and says plainly that hit rates are
undefined until RMs record responses. This is E4's acceptance test.

**Wave 4 is complete; so is the plan.**

