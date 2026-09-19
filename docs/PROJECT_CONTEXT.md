# DataInsights — condensed project context (for handoff to another Claude session)

**Purpose of this file**: a single, self-contained document capturing the
full context of this project — objective, architecture, decisions,
current state, and working conventions — so it can be pasted into a
Claude session in a different, more restricted environment (e.g. a
corporate machine that blocks/filters script uploads) and that session
can understand the project without seeing the actual codebase. Paste
this whole file as the first message, or as a system/context message,
before asking for help.

Last synced against the real repo: 2026-09-16.

---

## 1. What this is, in one paragraph

A proof-of-concept for a commercial/institutional banking **Next-Best-
Action (NBA)** system, built against NatWest's Federated Data Model
(`docs/fdm_reference.md`) and a captured engineering Decision Record
(`docs/decision_record.md`). Relationship managers today reach out to
clients on a calendar, not on a signal. This system watches a client's
**own** activity (endogenous: deposit buildup, facility utilization,
revenue-pattern shifts, credit-risk grade changes) and **external**
events near them (exogenous: a public tender award, matched against
that client's own confirming activity, not just sector/country
broadcast), assembles whatever fired for a client into one ranked,
sized, hypothesis-backed recommendation, and hands it to an RM as a
human-reviewed worklist — it enriches signals feeding Pega CDH, it does
not arbitrate or deliver to a channel itself (D1). A small set of local-
Ollama agents extract and narrate; none of them decide a category, a
score, or an offer size — that logic is deterministic Python throughout,
tested and traceable.

Scope: commercial/institutional clients only (SME / Mid-Corp / Large-
Corp / Institutional segments), European market, EUR-default.

## 2. Non-negotiable constraints (apply everywhere in this project)

- **Local inference only, no paid/cloud LLM calls, ever.** Enforced in
  code (`datainsights/config.py`'s validators reject Ollama `-cloud`
  tags, non-localhost endpoints, and any "paid calls allowed" flag) —
  every agent (`agents/model_factory.get_model()`) routes through this
  same validator.
- **Human review before any action.** Nothing in this system emails a
  client, writes to a CRM, or takes an action. It produces a reviewable
  recommendation; a person decides — and an RM's decision (Customer
  Engaged / Not Appropriate / Remind Me Later) is now actually captured
  (`datainsights/rm_feedback.py`), not just planned for.
- **No autonomous multi-step agent.** Every LLM call in this build is
  either narration-of-already-computed-facts (no tool access) or a
  single read-only investigation scoped to one client — never a chain of
  reasoning across domains, never a model that decides what to look at
  next.
- **Ground truth stays isolated from the detector.** Any synthetic label
  lives in a physically separate, code-blocked directory
  (`protected_evaluator_only/`) the detection code cannot import a path
  to. Enforced at three independent layers. No agent introduced this
  build (extraction, investigator, copilot) has an import path to it
  either.
- **Deterministic first, ML only when justified, LLM only where it earns
  its keep.** Rules before ML (an opt-in outlier-robust baseline exists,
  measured against the deterministic default, not defaulted-to); an LLM
  only at extraction (unstructured text → structured event), narration
  (facts → readable text), investigation (evidence → a proposed,
  unconfirmed refinement), and RM Q&A (one row → an answer) — always
  validated against the evidence, always able to fall back to a
  deterministic template, never the decision-maker.
- **No production deployment, no real external communication.** No AWS
  resource provisioning, no real MIMO/Pega/S3 publish, no emails/CRM
  writes — every sink that shapes toward one of those targets is
  schema-only and explicitly NOT RUN.

## 3. Architecture — one pipeline, a domain registry, and four narrow agents

```
FdmLocalSource (DuckDB/CSV, bi-temporal as-at)  [Snowflake adapter wired, NOT RUN]
  -> config/domains_fdm.yaml + datainsights/domain_registry.py   (domain registry, data half)
  -> agents/tools.py + agents/domain_registry.py                  (domain registry, code half --
                                                                    one register() call per domain)
  -> detection_engine/*.py   (7 FDM detectors + rating_downgrade, same Config/detect() shape)
  -> agents/domain_agent.py  (narrates verified facts -- NO tool access, validate-or-fallback)
  -> datainsights/correlation/  (Signal Bus -> Hypothesis Assembler -> De-dup)
  -> ONE Recommendation per client
  -> datainsights/sinks/  (RM worklist CSV + digest, MIMO JSON, Pega event mock --
                            none computes its own category/hypothesis/sizing)

Alongside, three narrow agents (docs/agentic_plan.md):
  external_events/event_extraction_agent.py   (A1: text -> validated ExogenousEvent, feeds the
                                                deterministic exposure_qualifier -- never decides exposure)
  agents/investigator_agent.py                (A2: for an `ambiguous`-flagged Recommendation, proposes
                                                a category refinement from a fixed set -- RM confirms)
  agents/rm_copilot_agent.py                  (A3: answers an RM's question from ONE worklist row,
                                                no tool, no DataSource access; captures RM feedback)
```

Key design point: adding a domain (proven with Risk's `rating_downgrade`)
needs one YAML block + one `register()` call — zero edits to
`agents/orchestrator.py`, `agents/domain_agent.py`, or
`datainsights/correlation/hypothesis.py`. Swapping the source backend
(local CSV → Snowflake) changes one adapter behind the shared
`DataSource` interface; `tests/test_fdm_source_conformance.py` proves
the two can't silently drift. Both guarantees are structural
(proven by a test that adds a throwaway domain/checks conformance), not
just claimed.

## 4. Where each kind of "intelligence" sits (and why)

| Stage | Technique | Why |
|---|---|---|
| Endogenous detection | **Statistical / rule-based** | Rolling baselines, threshold rules — explainable, no training data needed. One opt-in outlier-robust ML baseline exists (SLOT E2), measured against the deterministic default, not defaulting to it. |
| Exogenous event extraction (A1) | **LLM (local Ollama), validated** | Unstructured text has no rule to parse it with — this is the one place an LLM does work a rule genuinely cannot. Every field is validated in code before it becomes a real event; anything that fails goes to a review queue, never silently through. |
| Exogenous exposure qualification | **Rule-based, deterministic** | Sector + geography + a genuine confirming signal from the client's own data — a join and a threshold, not a model. The LLM in A1 never decides exposure. |
| Cross-domain correlation | **Deterministic Python** | The decision record's four composition rules, explicit and auditable — never an LLM. |
| Narrative (facts → text) | **LLM (local Ollama), no tool access** | Validated against the evidence before acceptance; template fallback on any failure. Never invents a fact, never calls a tool (M8/A0 — an earlier version gave it tools it didn't need, which just re-ran detection for no benefit). |
| Ambiguous-signal investigation (A2) | **LLM (local Ollama), read-only tools, one client** | For a signal whose category mapping is a disclosed simplification (e.g. `fixed_rate_expiry`). Proposes only; never changes the category itself. |
| RM Q&A (A3) | **LLM (local Ollama), zero tools** | Answers only from one worklist row's own fields — structurally unable to reach another client's data. |
| Category tagging | **Rule-based lookup** | `config/domains_fdm.yaml`'s per-signal-type category map, read by the domain registry — not a model. |
| Offer sizing (the amount) | **Rule-based, disclosed heuristic** | See §6 below. |
| Propensity / arbitration (SLOT E4) | **Not built, correctly** | Blocked on RM-response label access (D6) — Pega owns arbitration. `datainsights/rm_feedback.py` now captures that label going forward; the model itself stays unbuilt until real access exists. |

## 5. The six recommendation categories, and which ones are actual bank revenue

| Category | Bank revenue mechanism? | Mechanism |
|---|---|---|
| `FINANCING_NEED` | **Yes** | Interest income + origination fees on a new loan/credit line/guarantee |
| `TREASURY_OPPORTUNITY` | **Yes** | Fee/spread income from a deposit or short-term investment product |
| `HEDGING_NEED` | **Yes** | Fee income from an FX/commodity hedge |
| `CAPEX_FINANCING` | **Yes** | Interest income on equipment/transition financing |
| `RISK_REVIEW` | No — defensive | Credit/collateral/rating exposure review — protects existing revenue, isn't a sales trigger. Suppresses any revenue category for the same client (rule 1). |
| `ADVISORY_ONLY` | No — relationship | A conversation worth having, no clear product yet |

## 6. The hypothesis → sized action pattern

For every client with a recommendation: state the **general** hypothesis
for why the signal implies a need, then give a **specific, sized,
per-client action**.

1. **`hypothesis`** — one sentence of general reasoning, keyed by
   signal_type in `config/domains_fdm.yaml` (e.g.:
   *"Winning a public tender creates a cash-flow gap between delivery
   and payment... working capital sized to the contract is timely before
   delivery starts."*). Same sentence for every client matched to that
   signal_type; `datainsights/correlation/hypothesis.py` overrides it
   with a more specific line when exogenous confirmation applies.
2. **Sized `recommended_action`** — a concrete number per client, e.g.:
   *"Offer working-capital financing of ~EUR 800,000 (25% of the EUR
   3,200,000 tender value, illustrative) to bridge delivery before
   payment."*

How the number is grounded, honestly:
- An exogenous event with a real `estimated_value_eur` (from the TED-
  shaped fixture, or now from A1's own extraction) sizes a
  disclosed-percentage working-capital offer.
- Endogenous-only signals size from the client's OWN figures
  (`EndogenousSizing` in `hypothesis.py`) — e.g. `cash_buildup` offers
  the actual balance build-up amount; `facility_utilization_spike`
  offers a limit increase to a target utilization. Never a guess about
  revenue not on file.
- **Every heuristic figure is labeled "illustrative" inline**, and
  `sizing_basis` states the exact method as a machine-readable string
  (e.g. `25pct_of_event_value_illustrative`) — never a raw number with
  no disclosed method.
- `RISK_REVIEW` and `ADVISORY_ONLY` deliberately get **no** sized
  number — `datainsights/fdm_worklist.py`'s revenue model excludes them
  by name.

This is produced automatically for every `Recommendation`
(`datainsights/correlation/hypothesis.py::assemble()`), not ad hoc, and
lands in every sink: the worklist CSV, the RM digest, and (schema-only)
the MIMO/Pega records.

## 7. Inputs and outputs

### 7.1 Endogenous domains — what's built vs. deferred

| Domain | Status | Detectors |
|---|---|---|
| Deposits | **Built** | `cash_buildup`, `dormancy`, `revenue_pattern_change` |
| Lending | **Built** | `facility_utilization_spike`, `facility_maturity_approaching`, `fixed_rate_expiry`, `collateral_coverage_drop` |
| Risk | **Partially built** | `rating_downgrade` wired (reads `PARTY`'s real bi-temporal risk-grade history); `pd_migration` built and tested but **unwired** — needs `PARTY_METRIC`, which doesn't exist in the dataset or contract |
| Treasury | **Not started, blocked** | No confirmed C&I client-facing treasury data source exists at all (decision record's own Phase 6 finding) |

Every entity is bi-temporal (`EFFECTIVE_START_DT`/`EFFECTIVE_END_DT`)
where the FDM captures it that way — `config/entities_fdm.yaml` tags
each table's provenance honestly (real captured DDL, real DDL with
invented values, or fully invented, never silently blended).

### 7.2 Exogenous inputs

`external_events/exposure_qualifier.py`'s `ExogenousEvent` is fed two
ways today:
1. **The generator's fixture** (`external_events/output_fdm/tender_events.csv`)
   — a hand-shaped `public_tender_award` matched to a real
   exposed/unexposed client pair in the generated data.
2. **A1's extraction agent** (`external_events/event_extraction_agent.py`)
   — turns notice text into the same `ExogenousEvent` shape, validated
   (NACE section in the real set this dataset uses, ISO country code,
   date not after ingest, currency-grounded value, banned-term check).
   `agents/demo_fdm_scenario.py` prefers this source when it exists.
   Measured, live: TP=5, FP=0, FN=1 on 10 hand-written notices — zero
   false positives across every run, including while recall was 0%
   before a prompt fix.

Only `public_tender_award` has an exposure-qualification rule
implemented; the decision record names six other real, free sources
(ECB SDMX, Eurostat, EU sanctions/OpenSanctions, EUR-Lex, GDELT, EM-DAT)
as future extension points, each requiring its own qualification rule
before it can act — extraction alone doesn't make a new event type
usable.

### 7.3 Output — the outcome format, per sink

Every `Recommendation` carries category + hypothesis + sized action +
evidence + a stable `recommendation_id` (for future feedback-label
joins) + `baseline_source` (which SLOT E2 baseline produced it) +
`ambiguous` (whether A2 should look at it). Rendered, identically, by:

- **The RM worklist CSV** (`datainsights/fdm_worklist.py`) — one row per
  recommendation, ranked by indicative revenue.
- **The RM markdown digest** — the same fields as readable prose.
- **The Streamlit dashboard** (`dashboard/app.py`'s "FDM worklist" page)
  — filterable table, plus (M8/A3) an "ask about this recommendation"
  copilot panel and RM-response capture, live, not a mockup.
- **A MIMO-shaped JSON** and a **Pega event mock** — schema-only, NOT
  wired to a real platform; `tests/test_sink_contract.py` proves all
  sinks agree on category/hypothesis/action/id from one Recommendation.

**What is explicitly not an output**: no email, no CRM write, no
automated outreach, no real MIMO/Pega/S3 publish.

## 8. Current state — see `docs/current_state.md`

That file is kept current with every session's verified/NOT RUN status
and is the fresher summary — this section intentionally doesn't
duplicate it (duplication is how docs drift). Headline, as of the last
sync above: **296 tests passing** (∼290 without a reachable local
Ollama), both demo scripts and the Streamlit dashboard verified working,
including live end-to-end Ollama runs and real Streamlit `AppTest`
clicks (not just page loads) for the A3 copilot/feedback panel.

Two disclosed, unfixed issues worth carrying into any continuation:
1. **A2's investigator agent** produced a category proposal that
   contradicted its own stated evidence on a live run — current
   validation checks the category is in the allowed set, not that the
   reasoning supports it. Treat its proposals as unconfirmed.
2. **`pd_migration`** (Risk) is built and tested but unwired, correctly
   — blocked on data that doesn't exist, not forgotten.

## 8b. Performance and scale -- measured, not estimated

Measured on the real whole-book path (M16 in `docs/changelog.md`),
because "does this scale" had no documented answer before.

| Dataset | Clients | Rows materialised | Wall | ms/client |
|---|---|---|---|---|
| 182k rows, no cache | 300 | 98,063,596 | 139.4s | 465 |
| 182k rows, cached | 300 | 181,848 | 18.9s | 63 |

- Cost grew as **O(clients x table_size)** -- ms/client rose with dataset
  size (190 -> 465), the quadratic signature. `RunScopedCache` is now
  wired into `build_runtime()`, which flattens ms/client against book
  size.
- **That ceiling is now removed (R13, M22):** `CanonicalSource` used to
  re-merge and re-project the full table per client (860 reads
  re-assembling 2,373,916 rows for 60 clients). It now caches the
  projected frame per (concept, as_at) and slices a client by a position
  index; `evaluate_book` shares ONE `CanonicalSource` across the book.
  Measured: 300 clients 12.4 s -> 4.55 s, ms/client 41 -> 15 and flat
  with book size. Golden equivalence proven on a window with 75
  positives (`tests/test_set_based_book_evaluation.py`); the worklist is
  byte-identical. What remains per client is the detectors themselves.
- **There is no parallelism anywhere** -- no ThreadPool, multiprocessing
  or asyncio. `agents/orchestrator.py`'s registry loop is the intended
  split point (`docs/agentic_plan.md` §3).
- For a real bank book, the order of work is: predicate pushdown ->
  aggregate pushdown (return features, not rows) -> set-based batch.
  See `docs/ml_strategy_plan.md` §7 and T6.

## 8c. Self-service ML -- who decides a field is worth challenging, and how

Full design: `docs/ml_strategy_plan.md`. Baby-steps guide: `docs/ml_quickstart.md`.

**The question this answers**: given a NEW schema, on what basis does ML
ever apply to one of its fields, and who decides? Three gates, in order,
never an LLM alone:

1. **Gate 1, deterministic** (`onboarding/ml_profiler.py`): numeric, has
   a time column, >=8 observations/entity, >=30 entities, <=20% null,
   non-constant. No opinion on relevance, only on whether there's enough
   clean history for a comparison to mean anything.
2. **Gate 2, the binding** (already exists): a measure a schema's binding
   already maps to a canonical concept (`config/semantic_model.yaml`) is
   enabled automatically, no config needed -- it's right not because an
   algorithm guessed well, but because a human already gated acceptance
   when the binding was written.
3. **Gate 3, LLM proposal, cold-start only** (`onboarding/ml_measure_proposer.py`):
   for a schema-specific field the canonical model has never seen. Same
   propose-validate-reject discipline as every other agent here --
   `config/ml_policy.yaml` never updates from an LLM call alone, only
   from a human clicking Save (dashboard's **ML opportunities** tab) or
   hand-editing the YAML.

**Library**: scikit-learn only, deliberately not Keras/TensorFlow --
tabular, 8-30 observations per client, explainability is a hard SS1/23
requirement, and sklearn is already on the bank's Artifactory.

**Where models run**: E2 baselines (per-client, many small fits) stay
in-process, never behind an endpoint -- thousands of per-client SageMaker
endpoints is a cost/latency anti-pattern. E4 propensity (one whole-book
model, once RM-feedback labels exist) is the legitimate SageMaker case --
contract-only today (`datainsights/ml/backends/`), raises
`NotImplementedError` naming the exact blocker.

**Snowflake**: push compute to the data via predicate pushdown -> aggregate
pushdown (`datainsights/features.py::compute_many()`, proven for the flat
schema backend, not yet for FDM's bi-temporal one) -> Snowpark ML for E4
training only. Snowpark ML is explicitly NOT pre-authorized just because
it's a different category from the forbidden Cortex/AI_COMPLETE path --
it needs its own sign-off.

**Status, 2026-09-18**: T1-T9 of the plan all built and verified (see
`docs/changelog.md` M17). A user can scan any schema for ML-eligible
measures, set/save policy, and run a real champion/challenger comparison
from the dashboard -- for the `fdm` schema's 2 known E2 measures today;
legacy/sba report "not wired" honestly rather than fake a result.

## 9. Known limitations to state honestly if this work continues

- **Contamination**: any precision/recall number from a session that
  also wrote the injection/generation logic is a development
  diagnostic, not a validated detection-quality claim.
- **Illustrative heuristics, not benchmarks**: every sizing percentage
  is a disclosed planning assumption, not derived from real conversion
  data.
- **SLOT E2 disagreement isn't evidence of improvement**: the ML
  baseline and the deterministic one disagree on real data at scale, but
  there's no real outcome label on synthetic data to say which is
  right — keep this in mind before treating a "detected" from either
  baseline as ground truth.
- **A2's reasoning-consistency gap** (§8) — a proposal can currently be
  self-contradictory and still pass validation.
- **Correlated judge/narrator risk**: local Ollama models with unknown
  training-data overlap where more than one model is used in the same
  pipeline.
- **GDPR/EU AI Act**: checked, not assumed — this design sits outside
  both because matching operates on legal entities, never correlates a
  PEP/high-risk flag with a political event type, and produces no
  individual-level creditworthiness score. `HIGH_RSK_CUST_IND` is
  proven (by test) to only ever suppress a category, never scale a
  score or appear in narrative/agent text.

## 10. Repo structure (key files only)

```
config/entities_fdm.yaml                FDM source contract (bi-temporal, per-entity provenance)
config/domains_fdm.yaml                 domain registry -- data half (actions, category/hypothesis, ambiguity)
config/rules.yaml                       detector thresholds + SLOT E2 baseline selection
data_generator/fdm/generate_fdm.py      synthetic FDM dataset (--n-parties/--history-years for scale)
data_generator/output_fdm*/             the dataset; protected_evaluator_only/ is ground truth, code-blocked
datainsights/sources/fdm_local.py       FdmLocalSource -- DuckDB, bi-temporal as-at joins
datainsights/domain_registry.py         domain registry data-half accessors
detection_engine/*.py                   7 FDM detectors + rating_downgrade (pd_migration built, unwired)
agents/tools.py                         Strands @tool per detector, one register() call per domain
agents/domain_registry.py               domain registry code-half (tool factories, detector modules)
agents/domain_agent.py                  narrates verified facts, no tool access, validate-or-fallback
agents/investigator_agent.py            A2 -- proposes a refinement for an ambiguous signal
agents/rm_copilot_agent.py              A3 -- answers from one worklist row, zero tools
agents/orchestrator.py                  evaluate_client/evaluate_book -- the one pipeline entry point
agents/model_factory.py                 local Ollama now; Model Gateway branch NOT RUN
external_events/exposure_qualifier.py   deterministic 3-step exposure qualification
external_events/event_extraction_agent.py  A1 -- text -> validated ExogenousEvent
datainsights/correlation/               Signal Bus, Hypothesis Assembler, De-dup
datainsights/fdm_worklist.py            RM-facing worklist/digest builder
datainsights/sinks/                     MIMO JSON, Pega event mock, key_mapping, base
datainsights/agent_trace.py             per-narration audit trail, keyed on recommendation_id
datainsights/rm_feedback.py             RM response capture (Customer Engaged/Not Appropriate/Remind Me Later)
datainsights/ml/                        SLOT E2 baselines, compare_baselines.py, scale_evaluation.py
dashboard/app.py                        Streamlit dashboard -- FDM worklist page has the A3 copilot/feedback panel
docs/decision_record.md                 the captured NatWest engineering decision record -- check any new decision against this
docs/fdm_reference.md                   the captured Federated Data Model reference
docs/architecture.md                    full system design + diagram (FDM build is primary; legacy is an appendix)
docs/current_state.md                   verified vs. NOT RUN vs. not built, plus "Run it yourself"
docs/agentic_plan.md                    the agentic build plan (A0-A5), phase-by-phase execution status
docs/gap_analysis.md                    current state vs. decision record / agentic plan, table form
docs/adding_a_new_domain.md             step-by-step checklist for a new domain (proven with Risk)
docs/hardcoding_audit.md                architect's review: what is genuinely generic vs generic-by-shim vs hardcoded, with evidence
docs/refactor_plan.md                   THE PLAN: 12 tasks in 4 waves (R1-R12), trigger model, RM journey, where learning enters
```

## 11. Working conventions this project holds itself to

- **Verify, don't assert.** Every claim of "N tests pass" or "the
  pipeline ran" was backed by an actual run in that session. One
  concrete example this project holds itself to: a Step 3 scale-up once
  measured a 23x-per-client slowdown; before writing it down as a
  finding, it was profiled, re-run isolated, and traced to system
  contention from other concurrent work, not a real scaling cliff — the
  corrected number is what's in `docs/current_state.md`, with the false
  alarm disclosed alongside it, not hidden.
- **Separate verified facts from assumptions explicitly**, every time.
- **Disclose limitations inline, in the artifact itself.** The
  "illustrative" tags on every sized offer, and `sizing_basis`'s
  machine-readable method string, are direct examples.
- **One phase at a time.** Don't rebuild what already works; extend it —
  the domain registry (M7) and every agent in M8 were built on top of
  the existing detector/correlation layer, not instead of it.
- **No fabricated precision.** If a number isn't grounded in real data
  or a disclosed heuristic, don't state it.
- **An agent's validation failure degrades to a template/fact-list, never
  raises and never emits an unvalidated claim.** Every agent in this
  build (narrator, A1 extractor, A2 investigator, A3 copilot) follows
  this identically.

## 12. NatWest VDI handoff checklist — what changes when moving machines

1. **Swap the data source.** `FdmLocalSource` → a real
   `FdmSnowflakeSource` reading `ENT_PRD.TIER0_PRS` directly. Same
   `DataSource` interface — detector/agent/correlation code should not
   need to change if `config/entities_fdm.yaml`'s contract still holds
   against the real physical tables (it may not for tables this pass
   had to invent columns for — see `code_domains.py`'s
   `invented_domains()`).
2. **Swap the model provider.** `agents/model_factory.get_model()`'s
   `ModelConfig.mode` changes from `"local"` to `"model_gateway"`, and
   that branch (currently a documented `NotImplementedError`) gets a
   real internal Model Gateway client. No other agent code — narrator,
   A1, A2, A3 — should need to change; that's the entire point of the
   factory.
3. **Real Phase 0/1 first.** Nothing above substitutes for actual
   stakeholder conversations (MIMO AIEngine, C&I Decisioning) or a real
   MIMO insight against the live platform.
4. **Re-run Phase 7 SME validation** against real value domains once
   accessible — every `SOURCE = "INVENTED"` marker in `code_domains.py`
   needs replacing or explicit sign-off; Risk/Treasury's remaining
   column shapes need confirming before `pd_migration`/Treasury
   detectors can be built for real.
5. **Enter AgentCore governance for real** only with a working artefact
   and evidence in hand. Three of the governance table's requirements
   are already true by construction — human-in-the-loop, audit trail
   (`datainsights/agent_trace.py`), and feedback capture
   (`datainsights/rm_feedback.py`) — see `agents/README.md`'s table for
   what that does and doesn't cover.
6. **Real notice/news text for A1**, and enough real client data for A1
   extraction to have something to qualify against — on synthetic data,
   even a real notice about a real event only produces an illustrative
   result, since the client base is Faker output either way.

---

## Appendix: the legacy-schema pipeline (retired, R23)

The original CLI pipeline (`datainsights/cli.py`, `runner.py`,
`ranking.py`, `worklist.py`, `state.py`, `digest.py`, `status.py`,
`detection_engine/external_macro_event.py`, the macro narrator) was
deleted on 2026-09-19 — two pipelines were permanent cost and the
second one demonstrated the weaker path. What survived: its schema, as
`config/bindings/legacy.yaml` + `config/profiles/legacy_local.yaml`, run
by the one pipeline above; its `large_incoming_payment` detector, ported
canonically (same statistics, fires on 45 of 606 legacy clients);
`evaluation/evaluate.py`, kept as the only ground-truth reader until R18.
`docs/changelog.md` M25 records the move.
