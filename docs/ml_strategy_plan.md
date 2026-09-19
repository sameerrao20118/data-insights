# ML strategy and implementation plan

**Status: PLAN ONLY. Nothing in sections 3-10 is built.** Section 2 is the
verified inventory of what exists today. Written to be executed task by
task; each task in section 9 names its files, interfaces, acceptance
test, and explicit non-goals.

Read `docs/decision_record.md` Tab 6 (the four ML slots) and
`docs/agentic_plan.md` §3b (technology choices) first — this plan extends
them rather than restating them.

---

## 1. The question this plan answers

> "On what parameters do we decide ML applies to a new data model? Which
> field do we think we'd always be right about? Do we use LLM intelligence
> to find the important field? Can users nudge it? Keras or scikit-learn?
> Can users run this themselves? What happens on AWS/Snowflake at scale?"

The short answer to the hardest part first: **you can never be "always
right" about which field matters, so the architecture must not assume it
is.** Every design below treats field relevance as a *ranked proposal
with provenance*, never a fact — and records which of three mechanisms
chose each field, so a model-risk reviewer can audit the choice.

---

## 2. What exists today (verified, not aspirational)

| Piece | File | State |
|---|---|---|
| Four slot interfaces (E1 magnitude, E2 baseline, E3 qualifier, E4 propensity) | `datainsights/ml/slots.py` | Built. E1/E2/E3 have deterministic defaults; E4 is `UnavailablePropensityModel` and raises by design |
| sklearn challenger for E2 | `datainsights/ml/baselines.py` | Built. `IsolationForestBaseline` — outlier-robust median/MAD on one client's own history. Opt-in via `config/rules.yaml` `baseline:` key; deterministic stays default |
| Champion/challenger comparison | `evaluate_baselines.py`, `compare_baselines.py`, `scale_evaluation.py` | Built, developer CLI only. Writes run manifests to `var/ml_runs/*.json` |
| PIT feature primitive | `datainsights/features.py` | Built. `compute(series, as_of, date_col, value_col, window_days, agg)`. Deliberately not retrofitted into the 9 detectors |
| Label pipeline (E4 plumbing) | `datainsights/ml/label_pipeline.py` | Built. Joins `rm_feedback` to worklist rows. Empty until RMs respond — by construction, not a bug |
| Model registry + model card | `datainsights/ml/model_registry.py` | Built. `var/models/<name>/<version>/{model.joblib,card.json}`. Refuses to save without a valid card |
| Canonical measures | `config/semantic_model.yaml` | Built. The concepts ML can attach to |

**Libraries:** `scikit-learn==1.7.2`, `joblib==1.6.0`, numpy 2.5.2, pandas
3.0.5. No Keras, TensorFlow, PyTorch, XGBoost, LightGBM, Snowpark or
SageMaker SDK anywhere in `requirements.txt`.

**The gap this plan closes:** there is no path for a *user* to discover
which of their fields are ML-eligible, turn a challenger on for their own
schema, run a comparison, or read the result. Everything above is a
developer CLI.

---

## 3. Deciding where ML applies: three gates, in this order

Run cheapest-and-most-reliable first. An LLM is never gate 1.

### Gate 1 — Structural eligibility (deterministic, no LLM)

A column is ML-eligible only if **all** hold. These are the "parameters"
the decision rests on:

| Parameter | Default | Why |
|---|---|---|
| `inferred_type` ∈ {int, float} | — | A baseline models a magnitude. Categorical needs a different slot |
| Has an associated time column | — | Every slot is as-of; without time there is no PIT correctness |
| Observations per entity | ≥ 8 | `IsolationForestBaseline.min_observations` — below this a forest is noise, and it already raises `InsufficientHistory` |
| Entities with enough history | ≥ 30 | Below this, champion/challenger comparison has no power |
| Null rate | ≤ 20% | Above this, imputation decisions dominate the model |
| Non-constant | variance > 0 | A constant column cannot carry signal |
| Not an identifier | not `is_unique` | A key is not a measure — `profiler.py` already flags uniqueness |

`onboarding/profiler.py` already computes type, nullability, uniqueness,
and date columns. Gate 1 is an extension of it, not a new subsystem.

### Gate 2 — Semantic relevance (the binding already answers this)

**This is the key architectural point.** Detectors never read physical
columns; they read canonical concepts through a binding. So "which field
is ML-relevant" is already answered at onboarding: it is whichever
physical column a human accepted as a canonical *measure*.

The ML-relevant canonical measures are exactly:

- `BalanceObservation.balance` — the E2 baseline for `cash_buildup`
- `Transaction.amount` — the E2 baseline for `revenue_pattern_change`
- `PartyMetricVersion.value` — the E2 baseline for `pd_migration` (blocked on data)
- `CollateralValuation.value` — coverage-ratio baselining

A new schema inherits ML applicability **for free** the moment its
binding maps one of these. No per-schema ML configuration is required
for the common case. That is the payoff of the semantic layer, and it is
the honest answer to "which field would we always be right about": the
ones a human already accepted as canonical measures — right not because
an algorithm guessed well, but because acceptance was gated.

### Gate 3 — LLM proposal for unmapped extras (cold start only)

Some schemas carry measures the canonical model has no concept for
(`days_past_due`, `covenant_headroom_pct`). For these an LLM proposes
candidate measures + rationale, mirroring `onboarding/binding_proposer.py`
exactly: **propose → deterministic validation → human accept**. The LLM
may only choose from columns that already passed Gate 1, so it can never
nominate a key column or a constant.

Once a model exists, **statistical importance supersedes the LLM
permanently** (permutation importance on the fitted model). The LLM is a
cold-start heuristic, never a standing authority.

### Provenance is mandatory

Every ML-enabled field records `chosen_by: "policy" | "gate1+binding" |
"llm_proposal"` and that string goes into the model card. A model-risk
reviewer must be able to ask "why this field" and get an audited answer.

---

## 4. User control: policy as config, not code

Precedence, highest first: **explicit user policy > Gate 1 + binding >
LLM proposal.** A user can always overrule the machine; the machine can
never overrule the user.

New file `config/ml_policy.yaml` (one block per schema):

```yaml
schema: fdm
measures:
  BalanceObservation.balance:
    enabled: true
    slot: E2
    algorithm: isolation_forest      # | deterministic | robust_median
    hyperparameters: {min_observations: 8, contamination: 0.1, n_estimators: 20}
    chosen_by: policy
  Transaction.amount:
    enabled: false
    disabled_reason: "RM feedback says step-changes are seasonal here, not growth"
defaults:
  algorithm: deterministic           # production default stays deterministic
  require_challenger_win: true       # a challenger is not promoted on a tie
```

`require_challenger_win` encodes the existing discipline: the
deterministic baseline stays the default until a comparison says
otherwise on real data.

---

## 5. Which library: scikit-learn. Not Keras.

**Decision: scikit-learn only.** Not Keras/TensorFlow/PyTorch.

1. The problems are tabular, per-entity, and small-N (8-30 observations
   per client). Deep learning has no advantage and considerable downside
   at this shape.
2. **Explainability is a hard requirement, not a preference.** SS1/23
   model risk and AIRA review will ask how a score was produced. A
   median/MAD over outlier-filtered points is explainable in a sentence;
   a neural net is a governance project.
3. `scikit-learn==1.7.2` is already installed and on the bank's
   Artifactory (`docs/decision_record.md` Tab 7). Keras/TF are not —
   adding them means a Security STaRT/DRA cycle for zero capability gain.
4. The measured machine is 2 cores, no GPU.

**Revisit trigger** (write it down so the decision can be reopened
honestly): if E4 propensity, once labels exist, needs interactions that a
calibrated linear model demonstrably cannot capture — go to sklearn
`HistGradientBoostingClassifier` first, still explainable via SHAP.
LightGBM/XGBoost only after an Artifactory availability check. Deep
learning remains out of scope for this product.

---

## 6. Where models run: today, and on AWS

Match compute to workload shape. The two ML slots have *opposite* shapes
and must not be deployed the same way.

| Slot | Shape | Local today | AWS target | SageMaker? |
|---|---|---|---|---|
| **E2 baseline** | Thousands of tiny per-entity fits, fit-on-read, no persistence | In-process sklearn | Same code inside the Glue/Batch container that runs the book | **No.** Thousands of per-client models behind endpoints is an anti-pattern and a cost trap |
| **E4 propensity** | ONE model over the whole book, trained periodically, scored in batch | Blocked on labels | SageMaker **Training Job** + **Model Registry** + **Batch Transform** | **Yes — this is the legitimate case** |

**No real-time SageMaker endpoints initially.** The worklist is a batch
product. Even the AgentCore per-request path should load the joblib
artifact in-process — a network hop per RM click buys latency and cost
for nothing at this scale. Add an endpoint only when a genuine
sub-second, per-request scoring requirement appears.

`model_registry.py` was deliberately built to mirror a registry's shape:
the later swap is `save_model()` → SageMaker Model Registry, with
`ModelCard` remaining the governance artifact. The card already carries
purpose/data window/features/metrics/limitations/owner — which is close
to what MRM asks for; keep it as the source of truth rather than
re-inventing lineage in SageMaker.

---

## 7. Snowflake: push compute to the data

**Yes — and the `DataSource` contract already makes it possible.** The
rule: move compute to the store when `rows_scanned × row_width ≫
result_size`. Feature aggregation qualifies overwhelmingly. Narration
does not.

Three stages, in dependency order:

1. **Predicate pushdown.** Today `CanonicalSource.read()` pulls whole
   tables with `allow_unbounded=True` and filters in pandas. Add
   `party_ids`/`account_ids` parameters that become a SQL `WHERE` for
   sources that support it. Biggest correctness-preserving win.
2. **Aggregate pushdown — the decisive one for millions of rows.**
   `features.py` currently computes windowed aggregates in pandas. Give
   it a SQL-emitting path so Snowflake computes per-entity windowed
   aggregates server-side and returns *features, not rows*. This is what
   turns "pull 50M balance rows to a laptop" into "pull 50k feature
   rows".
3. **Training near the data (E4 only).** Snowpark ML running ordinary
   sklearn in a Python UDF/stored procedure, for the single whole-book
   model where moving the training set out is the expensive part.

**Boundary, stated precisely:** `CLAUDE.md` forbids Snowflake
Cortex/`AI_COMPLETE` — that is a *paid LLM inference* service and stays
forbidden. Snowpark ML running sklearn is a different category (ordinary
compute, no model-provider call), but it is **not pre-authorized** by
that distinction: it needs its own explicit sign-off before any code
targets it. Do not treat this paragraph as authorization.

**No feature store yet.** Adopt one only when a second consumer needs the
same features. Today there is one.

---

## 8. Governance thread (what makes this NatWest-ready)

Carry these through every task; they are the commercial differentiator:

- **No model decides anything.** Category, sizing and ranking stay
  deterministic. ML only ever sharpens a *baseline* (what counts as
  normal for this client). Preserve this property — it is what keeps
  AIRA tiering low and the SS1/23 conversation short.
- **Never trains on ground truth.** No ML code may read
  `protected_evaluator_only/`. `tests/test_guardrails.py` enforces this
  statically — extend its allowlist consciously, never reflexively.
- **Every model needs a card.** Already enforced by `save_model()`.
- **Champion/challenger, never silent replacement.** Deterministic stays
  default until a measured comparison on real data says otherwise.

---

## 9. Executable tasks -- **T1-T9 all executed, 2026-09-18** (see `docs/changelog.md`'s M17 section for full verification detail: 439 -> 477 tests passed, every claim below is backed by a real run, not an intention)

Each is independently shippable. Do not batch them into one PR.

### T1 — ML-eligibility profiler (Gate 1) -- **DONE**
- **New:** `onboarding/ml_profiler.py`; **tests:** `tests/test_ml_profiler.py`
- **Interface:** `assess(profiles: dict[str, TableProfile], *, min_observations=8, min_entities=30, max_null_rate=0.2) -> dict[str, list[MeasureEligibility]]` where `MeasureEligibility` carries `table, column, eligible: bool, reasons: list[str], observations_per_entity: float`.
- **Must:** reuse `onboarding/profiler.py`'s existing type/uniqueness/date inference. Reject unique columns and zero-variance columns with an explicit reason string.
- **Acceptance:** run against `data_generator/output_fdm/` and assert `agreement_daily_balance.AGRMNT_LDGR_BAL_AMT` is eligible and `AGRMNT_ID` is rejected as an identifier. Deterministic — no Ollama.
- **Non-goal:** no LLM, no model fitting.
- **Verified:** ran against the real shipped FDM data -- `agreement_daily_balance.AGRMNT_LDGR_BAL_AMT` eligible (entity_column=AGRMNT_ID, 128.2 observations/entity); `party.RSK_GRD_VAL` correctly rejected (1.2 obs/entity, below the floor); every string/identifier column never even assessed. 6/6 tests pass.

### T2 — `config/ml_policy.yaml` + loader -- **DONE**
- **New:** `config/ml_policy.yaml`, `datainsights/ml/policy.py`; **tests:** `tests/test_ml_policy.py`
- **Interface:** `load_policy(schema: str) -> MlPolicy`; `MlPolicy.resolve(measure: str) -> ResolvedMeasure` with `enabled, algorithm, hyperparameters, chosen_by`.
- **Must:** implement the precedence in §4 (policy > gate1+binding > llm) and validate that `algorithm` is a registered name; unknown algorithm is a config error naming the valid set.
- **Acceptance:** a policy disabling a measure wins over an eligible Gate-1 result; an unknown algorithm raises with the valid options listed.
- **Verified:** 8/8 tests pass, including the real shipped `config/ml_policy.yaml` resolving `BalanceObservation.balance` to `enabled=True, algorithm=isolation_forest, chosen_by=policy`.

### T3 — LLM measure proposer (Gate 3) -- **DONE**
- **New:** `onboarding/ml_measure_proposer.py`; **tests:** `tests/test_ml_measure_proposer.py` (deterministic, stub model) + one live test
- **Must:** mirror `onboarding/binding_proposer.py` structurally — one call per candidate, pydantic-validated, **every proposed column must have passed Gate 1 or be dropped and logged in `rejected`**. Never writes config; emits a proposal a human accepts.
- **Acceptance:** a stub model proposing a non-existent or Gate-1-failing column has it dropped and surfaced in `rejected`, with nothing written.
- **Verified:** 3 deterministic tests (a failing model call is rejected not a crash; ineligible columns never proposed; already-mapped columns never re-proposed) + 1 LIVE test against real Ollama -- correctly recognised `AGRMNT_LDGR_BAL_AMT` as relevant with confidence > 0.

### T4 — Champion/challenger runner (the self-service core) -- **DONE, fdm schema only (disclosed)**
- **New:** `datainsights/ml/runner.py`; **tests:** `tests/test_ml_runner.py`
- **Interface:** `python -m datainsights.ml.runner --profile <name> [--measure <canonical.field>]`
- **Must:** for each enabled measure, run deterministic vs configured challenger over the book, write a manifest to `var/ml_runs/<run_id>.json` in the shape `scale_evaluation.py` already uses (`n_agreements`, `n_disagree`, runtime, data fingerprint). Reuse `RunScopedCache`. State plainly in output that disagreement ≠ improvement without outcome labels.
- **Acceptance:** produces a manifest for `fdm_local`; identical inputs give identical fingerprints.
- **Verified:** a real run against `fdm_local` -- 60 agreements, 3 disagree, deterministic detected 15 / isolation_forest detected 12, manifest written with the disagreement caveat inline. `Transaction.amount` correctly SKIPPED (policy resolves it to `deterministic`, nothing to compare) rather than faking a comparison. 5/5 tests pass.

### T5 — Dashboard "ML" tab (see §10 for the wider UI work) -- **DONE**
- **Edit:** `dashboard/app.py`
- **Must:** (a) eligibility table from T1 with per-column reasons; (b) policy toggles writing `config/ml_policy.yaml` **behind an explicit Save, never on widget change**; (c) a "Run champion vs challenger" button calling T4; (d) results with the disagreement caveat inline; (e) model registry listing with cards.
- **Acceptance:** `AppTest` loads the tab with no exception when no policy file, no manifests and no models exist yet.
- **Verified:** `AppTest` across all 10 tabs (OK); interactively clicked Scan, switched schema fdm->sba, toggled a policy checkbox, and clicked Save -- all exception-free. One real finding from that interactive test: the Save button writes the real `config/ml_policy.yaml`, so testing it live DOES mutate repo state -- caught, and the test-added `sba` section was reverted before this session ended; a future dashboard test should use a tmp path.

### T6 — Aggregate pushdown (the scale fix) -- **DONE for OfflineLocalSource (disclosed scope)**
- **Edit:** `datainsights/features.py`, `datainsights/sources/base.py`, `fdm_snowflake.py`; **tests:** `tests/test_feature_pushdown.py`
- **Must:** add `capabilities().supports_aggregate_pushdown`; `features.compute_many()` emits SQL where supported and falls back to the existing pandas path otherwise. **Golden test asserting both paths return identical values** — the whole point is that the optimisation is invisible in results.
- **Non-goal:** no Snowflake execution (no credentials). Contract + local DuckDB proof only.
- **Scope cut, disclosed:** built for `OfflineLocalSource` only (the flat-schema backend `legacy`/`sba` use) -- `FdmLocalSource`'s bi-temporal as-at collapse is NOT covered; pushing that down correctly would need to replicate CanonicalSource's bi-temporal logic in SQL, real undone work.
- **Verified:** measured on real SBA `balances` data (400 accounts) -- pushdown (DuckDB SQL) and pandas fallback agree to < 1e-6 across mean/sum/count/last/first, including a test where a source LIES about supporting pushdown but has no `aggregate()` method (must fall back, not crash). 8/8 tests pass.

### T7 — RM entitlement vertical slice (deferred P0) -- **DONE**
- **Edit:** `config/semantic_model.yaml` (`Party.relationship_manager_id`), `config/bindings/fdm.yaml`, `config/entities_fdm.yaml`, `data_generator/fdm/generate_fdm.py`, `datainsights/fdm_worklist.py` (`RM_WORKLIST_COLUMNS`)
- **Must:** derive the RM id **deterministically from `PRTY_ID`** (e.g. stable hash → `RM001..RM020`) so the generator's random stream is unchanged and every other entity regenerates byte-identically. Mark the field optional in the semantic model so `legacy`/`sba` bindings that lack it degrade honestly rather than crash.
- **Acceptance:** worklist carries `relationship_manager_id`; dashboard can filter by it; `legacy`/`sba` still run with the field absent.
- **Why it matters commercially:** without this the worklist cannot be routed or access-scoped to an RM — a blocker for any bank pilot.
- **Verified:** regenerated all 3 FDM datasets and confirmed the RNG stream stayed aligned (only `party.csv` gained the new column; the event generator still picked the same PRTY00036/37 pair it always has). Live run: 36-row worklist, 16 distinct RMs represented, 0 empty RM ids. `legacy` binding confirmed to degrade honestly (no crash, column simply absent) -- and its disclosure comment now notes the real RM data sitting uncontracted in `clients.csv`, a concrete next step. 4/4 tests pass.

### T8 — AWS/Snowflake ML adapter contracts (no deployment) -- **DONE**
- **New:** `datainsights/ml/backends/` with `sagemaker_training.py`, `snowpark_training.py`
- **Must:** follow the repo's existing convention exactly — construct from a profile, then raise `NotImplementedError` naming the precise blocker (no credentials, no authorization). Never a silent local fallback.
- **Acceptance:** a test asserts each raises with its named reason.
- **Verified:** both configs construct with zero network calls; both `train()` calls raise naming the exact blocker (no credentials + no CLAUDE.md authorization for SageMaker; no credentials + Snowpark-specifically-not-authorized-despite-being-a-different-category-from-Cortex for Snowpark). 4/4 tests pass.

### T9 — Documentation -- **DONE**
- **Edit:** `docs/PROJECT_CONTEXT.md` (the file that travels to the NatWest machine — add an ML-policy section **and the missing performance section**), `docs/gap_analysis.md` (add the perf row), `docs/architecture.md`.
- Add `onboarding/README.md` — the single "onboard a new schema" page that today is scattered across four places.

---

## 10. UI improvements (beyond the ML tab)

Ordered by how much confusion each removes for a first-time user.

1. **Guided first-run path.** The sidebar presents 9 peers with no
   ordering. Add a numbered "Start here → 1 Pick data → 2 Run → 3 Review
   worklist" strip on Overview that deep-links into the right tab. A new
   user currently cannot tell where to begin.
2. **Make state visible before it's needed.** Each source should show a
   status chip — *data generated? pipeline run? results exist?* — so
   "nothing happens" never requires guessing. Today a user can click Run
   with no data generated and only learn from a traceback.
3. **Explain the wait.** Whole-book runs take seconds and live narration
   ~15-20s with no progress feedback. Add a progress indicator with the
   expected duration *before* the run starts.
4. **Put the caveat next to the number, not in a paragraph.** Revenue
   figures are illustrative; that belongs as a small inline marker on the
   figure itself (a superscript that expands), which survives screenshots
   into a stakeholder deck. A caption above the table does not.
5. **Lead with the hypothesis, then the number.** The recommendation card
   should read *category → hypothesis → sized action* in that order —
   the documented canonical outcome shape (`PROJECT_CONTEXT.md` §7.3).
   Keep the UI faithful to it.
6. **Empty states should teach.** Replace "No worklist yet" with the
   exact command plus a button that runs it.
7. **One vocabulary.** "Profile", "binding", "schema", "data source" and
   "concept" are used near-interchangeably. Pick one user-facing term per
   idea and add a short glossary; this is the single biggest source of
   confusion for a banker rather than an engineer.
8. **Make evidence one click from every row.** `evidence_ref` is the
   trust anchor — an RM asking "why am I seeing this?" should never have
   to change tab.
9. **Separate "demo" from "operate".** Proof/verification buttons and the
   RM worklist serve different audiences; visually distinguish them so a
   business stakeholder is never looking at pytest output.
10. **Accessibility pass.** Category colour is currently the only channel
    encoding severity — add shape/text so it survives colour-blindness and
    greyscale printing.
