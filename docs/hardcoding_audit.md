# Hardcoding audit — how generic is this really, and what would make it so

**Status: REVIEW + PLAN. Nothing in §4 is built.** Every finding in §2 is
backed by a specific grep or file read on 2026-09-18 (file:line cited);
none is an impression.

## 1. The honest verdict, first

The stated goal: an accelerator that reads Snowflake / Glue / Postgres /
SQL Server, takes **any functionally related set of tables**, derives
endogenous + exogenous insight, correlates them, applies industry-standard
hypotheses, and suggests actions.

What is actually built: a pipeline that is generic across **any schema
that maps onto seven fixed banking concepts**, proven for real on three
structurally different schemas with zero detector/agent/correlation code
changes. That claim is true and tested. It is a *narrower* claim than the
goal, and the gap between the two is the subject of this document.

Three layers, three different truths:

| Layer | Genericity claim | Reality |
|---|---|---|
| **Correlation / arbitration** (`datainsights/correlation/`) | Generic | **Genuinely generic.** Keyed by canonical `signal_type`; reads `config/domains_fdm.yaml`. Leave it alone. |
| **Source contract** (`datainsights/sources/base.py`) | Generic | **Genuinely generic.** The ABC requires only `capabilities()` + `read_entity()`. The *shape* is right; the *breadth* is 2 working local backends, 2 Snowflake backends never run, and no Glue/Postgres/SQL Server at all. |
| **Detectors** (`detection_engine/`) | Generic via the semantic layer | **Generic by shim.** All 9 detectors speak FDM physical column names. `CanonicalSource` translates physical→canonical, then `agents/tools.py` translates canonical→**FDM-physical again** through six rename maps before calling a detector. It works — and every new detector inherits FDM vocabulary, every new concept needs a new map, and the guard test can't see it. |

The most important sentence in this document: **"any schema" and "any set
of functionally related tables" are different products.** The first is a
binding problem (solved). The second requires the semantic model to be a
registry rather than a constant, and even then needs the concept of a
*domain pack* — because detectors and hypotheses are inherently
domain-specific. See §3.

## 2. Findings — tiered by whether they block the stated goal

### Tier 1 — structural: these are what stand between today and the goal

| # | Finding | Evidence | Why it matters |
|---|---|---|---|
| 1.1 | **Detectors are FDM-native, not canonical.** | All 9 `REQUIRED_COLUMNS` in `detection_engine/*.py` use FDM physical names (`AGRMNT_LDGR_BAL_AMT`, `FIN_EVNT_PSTD_DT`, `RSK_GRD_CD`, …). | Genericity is achieved by back-translation, not by design. Every new detector must be written in FDM vocabulary or get its own rename map. |
| 1.2 | **Seven back-translation shims.** | `agents/tools.py:48-60` — `_ACCOUNT_RENAME`, `_BALANCE_RENAME`, `_TRANSACTION_RENAME`, `_DIRECTION_TO_SBTYP`, `_COLLATERAL_RENAME`, `_RISK_GRADE_RENAME`; plus an inline one in `external_events/exposure_checks.py:74-76`. | These are the hidden coupling. They exist *only* because 1.1 exists. Delete 1.1 and they all go. |
| 1.3 | **The semantic model is a fixed constant, duplicated in code.** | `config/semantic_model.yaml` declares 7 concepts. `onboarding/binding_proposer.py:27` re-declares them as a Python dict (`SEMANTIC_CONCEPTS`) instead of loading the YAML — even though `datainsights/semantic/binding.py:20` already has `SEMANTIC_MODEL_PATH`. | Adding an 8th concept means editing two places that can silently drift. And onboarding can only ever *map onto* the 7; it cannot propose a concept the schema needs that the model lacks. |
| 1.4 | **Business content lives in Python, not config.** | 8 dicts across 4 files: `REVENUE_MECHANISM`, `TALKING_POINT`, `WHY_NOW` (`fdm_worklist.py:44/57/84`); `MACRO_HYPOTHESIS` (`worklist.py:52`, 11 entries); `REVENUE_CATEGORIES`, `NON_REVENUE_ACTION`, `SUPPRESSED_ACTION` (`correlation/hypothesis.py:43/126/134`); `CATEGORY_INFO` (`dashboard/app.py:44`). | This is the "industry best standards hypothesis" layer the goal names — and it is code. A bank cannot tune it without a developer. Categories are hardcoded in ~5 places; domains are registry-driven. The inconsistency is the tell. |
| 1.5 | **No Glue, Postgres, or SQL Server source exists.** | `grep psycopg\|pyodbc\|sqlalchemy\|athena\|glue` in `datainsights/` hits only the `NotImplementedError` branches in `runtime.py`/`config.py` and a SageMaker stub. Snowflake: `FdmSnowflakeSource` exists, requires `domain_schema_map`, and `Profile` (`datainsights/config.py`) has **no field for it** — `runtime.py` raises. Never run. | The stated starting point (Snowflake + Glue) is one config field, credentials, and a first real run away for Snowflake; Glue does not exist. Postgres/SQL Server do not exist. |

### Tier 2 — bypasses that quietly undermine the guarantee

| # | Finding | Evidence | Why it matters |
|---|---|---|---|
| 2.1 | **Four modules bypass the canonical layer entirely.** | `agents/demo_fdm_scenario.py` (2 calls), `datainsights/ml/compare_baselines.py` (4), `evaluate_baselines.py` (1), `scale_evaluation.py` (1) call `source.party()`, `.agreement()`, `.daily_balance()`, `.financial_event()`, `.party_demographic()`, `.party_locator()` — methods that exist only on `FdmLocalSource`. | This is why the whole-book worklist and the champion/challenger runner work for `fdm` only. Confirmed by running them against `legacy_local` and watching them fail (M16, M17). |
| 2.2 | **The anti-coupling guard is loose.** | `tests/test_no_source_specific_coupling.py` scans **5 files** for **5 table-level names** (`PARTY_AGREEMENT`, `MORTGAGE_AGREEMENT`, two `.csv` filenames, `agreement_ledger_balance`). No column names; none of `detection_engine/`, the demo scripts, the ML scripts, or `exposure_checks.py`. | Every finding in 1.1, 1.2 and 2.1 passes this test. A guard that can't see the coupling it guards against gives false confidence. |
| 2.3 | **`product_codes` is dead config with a doc that vouches for it.** | `datainsights.domain_registry.product_codes()` has **zero** callers outside tests. `agents/tools.py:110-258` hardcodes `["deposit"]`, `["facility"]`, `["mortgage"]`. `docs/adding_a_new_domain.md:173` says "step 5's `product_codes()` lookup reads this." | A new contributor following the authoritative checklist edits a field that does nothing. |
| 2.4 | **Detector thresholds are one global file.** | `config/rules.yaml` — `cash_buildup.min_prior_balance: 5000` etc. `runtime.py:129` loads exactly one path; nothing per-binding. | A schema in a different currency or client scale must retune the shared file and silently retune every other schema with it. Not broken today (SBA's USD scale happens to land near FDM's EUR scale) — but structurally wrong for the goal. |
| 2.5 | **Exogenous coverage is split across two mechanisms.** | Legacy path: 11 `(event_type, direction)` hypotheses hardcoded in `worklist.py`. FDM path: `config/event_types.yaml` registers **2** types. The exposure checks (`exposure_checks.py`) read canonical concepts — good — then back-translate to FDM names (1.2). | Two pipelines tell different stories for the same event type, and only one is config-driven. |

### Tier 3 — demo residue: cheap, but it accumulates

`date(2025, 10, 4)` hardcoded as the evaluation date in `runner.py`, `compare_baselines.py`, `scale_evaluation.py`; `PRTY00036` as the default client in `dashboard/app.py` and both demo scripts; the string `"fdm_local"` / `load_binding("fdm")` as a literal in 7 places in the dashboard and 1 in `runtime.py`; `DOMAIN_READS` (dashboard lineage map) maintained by hand because no registry says which concepts a domain reads. None of these block anything. All of them are the sediment of "just make the demo work" that the goal will eventually trip over.

## 3. The ML question, answered directly

The concern was: ML is being applied to small datasets without the history to justify it. **That concern is correct.**

What SLOT E2 actually does: `IsolationForestBaseline` (`datainsights/ml/baselines.py`) fits a forest **per entity** on that entity's own last `max_history=30` observations of **one** variable, drops the points it calls outliers, and returns a median/MAD of what remains. That is *outlier-robust univariate statistics*, not machine learning in any sense a model-risk reviewer would recognise. It never learns across clients, never sees a label, and its "training set" is at most 30 numbers.

The eligibility gate (`onboarding/ml_profiler.py`): `min_observations=8`, `min_entities=30`. These are not derived from statistical power. They are the smallest values at which the shipped demo data passes — FDM has 92 deposit accounts × 131 observations; SBA 400 × 61. On that data a "challenger" that disagrees with the deterministic baseline on 3 of 60 accounts (M17's real run) is noise-level, and there are no outcome labels to say which side of any disagreement is right.

**Proposal:**

1. **Call E2 what it is.** Rename it a *robust baseline*, not an ML challenger. Keep it — it's a good technique — but stop presenting it as the ML story.
2. **Make the gate principled.** Replace the demo-tuned floors with a stated power criterion: a challenger is eligible only when (a) there are enough entities for a *cross-entity* model to be estimated, (b) enough per-entity history that a 30-point window isn't the whole record, and (c) an evaluation protocol exists. Below that, the ML tab should say, plainly, **"no ML applies to this dataset yet — deterministic baselines are the right choice here"**. For today's FDM data that is the correct answer, and the tab currently implies otherwise.
3. **Reserve "ML" for where it earns it.** SLOT E4 — one propensity model across the whole book, trained on RM-feedback labels. That is real machine learning, and it is honestly blocked on label volume (D6). The self-service tab, the policy file, and the registry were built as plumbing for it; the plumbing is fine, the water isn't there yet.

## 4. Target architecture — what "generic" should mean

```
                 ┌─────────────────────────────────────────────────────┐
  any backend ── │ DataSource ABC: read_entity() + aggregate() + caps  │   (already right)
  (Snowflake,    └──────────────────────┬──────────────────────────────┘
   Glue/Athena,                         │ physical rows
   Postgres,                            ▼
   SQL Server,   ┌─────────────────────────────────────────────────────┐
   CSV/DuckDB)   │ Binding (config/bindings/<schema>.yaml)             │   (already right)
                 └──────────────────────┬──────────────────────────────┘
                                        │ canonical rows
                                        ▼
                 ┌─────────────────────────────────────────────────────┐
                 │ Semantic model = REGISTRY (loaded, extensible)      │   ← R4: today a constant
                 │   grouped into DOMAIN PACKS (banking = 7 concepts)  │
                 └──────────────────────┬──────────────────────────────┘
                                        │ canonical names ONLY from here down
                                        ▼
                 ┌─────────────────────────────────────────────────────┐
                 │ Detectors: declare REQUIRED_FIELDS in canonical     │   ← R1: today FDM-native
                 │ names; no rename layer exists                       │
                 └──────────────────────┬──────────────────────────────┘
                                        │ Signals (canonical, already)
                                        ▼
                 ┌─────────────────────────────────────────────────────┐
                 │ Correlation + hypotheses, ALL content from config   │   ← R2: today 8 Python dicts
                 └─────────────────────────────────────────────────────┘
```

The unit of extension becomes the **domain pack**: a set of canonical
concepts + the detectors that read them + the categories/hypotheses they
produce + default thresholds. Banking is the first pack. "Any
functionally related tables" then means: *any tables that a binding can
map onto some pack's concepts* — and if no pack fits, the accelerator's
honest answer is "propose a new pack", not a crash and not a guess.

## 5. Refactor plan — sequenced by leverage, each independently shippable

| # | Refactor | Removes | Effort | Acceptance |
|---|---|---|---|---|
| **R1** | **Detectors go canonical.** Each `REQUIRED_COLUMNS` becomes canonical field names (`balance`, `observed_at`, `account_id`, …). `agents/tools.py` passes `CanonicalSource.read()` output straight through. | All 7 rename maps (1.2); the "generic by shim" caveat (1.1) | Medium — 9 detectors, mechanical, but every detector unit test's fixture columns change too | `grep AGRMNT_\|FIN_EVNT_\|RSK_GRD_ detection_engine/ agents/ external_events/` returns nothing. All 483 tests pass with fixtures renamed. |
| **R2** | **Business content → config.** Move the 8 dicts to `config/categories.yaml` (categories, revenue mechanism, display info, revenue/non-revenue sets) and into `config/domains_*.yaml` (why-now, talking points). Categories become registry-driven like domains. Fold the 11 legacy `MACRO_HYPOTHESIS` entries into `config/event_types.yaml` so there is one exogenous registry. | 1.4, 2.5, and the ~5-place category hardcoding | Low | Adding a category is a YAML edit + nothing else; a test proves a dummy category flows to the worklist and dashboard. |
| **R3** | **Close the bypasses and tighten the guard.** Rewrite the 4 modules in 2.1 through `CanonicalSource`. Extend `test_no_source_specific_coupling.py` to (a) every package except `datainsights/sources/`, (b) column names from every `entities_*.yaml`, (c) the FDM method names. Delete `product_codes` or wire it; fix the doc. | 2.1, 2.2, 2.3 | Low-medium | The whole-book worklist and the champion/challenger runner run for `legacy` and `sba`, not just `fdm`. The guard fails if anyone reintroduces a rename map. |
| **R4** | **Semantic model as a registry; introduce domain packs.** Load concepts from YAML everywhere (delete `SEMANTIC_CONCEPTS`). Add `kind:` (entity / observation / event / version) so the profiler's Gate 1 and the pushdown layer can reason about shape generically. Let onboarding **propose a concept**, not just a mapping, when a Gate-1-eligible table matches nothing — same propose/validate/human-accept discipline. | 1.3; the "7 fixed concepts" ceiling | High | A schema with a table no existing concept covers yields a *proposed concept* for review, not an "unavailable". |
| **R5** | **Source breadth.** One `SqlSource(DataSource)` over a dialect-aware connector (SQLAlchemy is on most Artifactory mirrors; verify per Tab 7) implementing `read_entity` + `aggregate` for Postgres / SQL Server / Snowflake, with dialect-specific quoting and pushdown. `AthenaSource` for Glue via the same contract. Add `domain_schema_map` to `Profile`. | 1.5 | Medium to build; **blocked on credentials to actually run** | Conformance test (`test_fdm_source_conformance.py` already exists) passes for every backend against the same fixture. Real runs stay marked NOT RUN until they happen. |
| **R6** | **Per-binding threshold overrides.** `config/rules.yaml` keeps global defaults; `config/bindings/<schema>.yaml` may carry a `rules:` override block that `build_runtime` merges. | 2.4 | Low | SBA can set its own `min_prior_balance` without touching FDM's. |
| **R7** | **ML gate becomes principled; E2 relabelled.** Per §3. The ML tab says "no ML applies here" when that is true. | The credibility problem in §3 | Low | On today's FDM data the tab reports no eligible ML challenger, and says why. |
| **R8** | **Profile proposer.** `onboarding.propose` also emits `config/profiles/<name>_local.yaml`; `accept` writes it. | The last manual step in onboarding | Low | Accept → `python -m agents.demo_fdm_scenario --profile <name>` runs with no hand-edits (requires R3). |

**Order:** R1 → R2 → R3 first. They are the foundation, they remove the
most coupling per hour, and R3's tightened guard then protects R1
permanently. R4 is the big architectural step and should not start until
R1 has made the detector layer clean. R5 is needed for the stated goal but
is blocked on credentials for any *real* verification. R6–R8 are small and
can slot in anywhere.

## 6. The agent architecture — what the "agents" actually are

Asked directly: "each agent is a Strands agent invoked in a loop — is
that a production design? I expected one agent to call multiple lookups
and reason, and agents to link across domains." Two verified facts
answer this.

**Fact 1 — the domain agents have no tools.** `agents/domain_agent.py:308`:
`Agent(model=self.model, tools=[], system_prompt=...)`. Every detector
runs deterministically first in `_gather_tool_evidence()`; the LLM is
then handed a pre-computed facts block and asked to restate it, and its
output is validated against those facts before acceptance. The code
comment records that tools were *removed* deliberately: the model was
re-running the same detectors the code had already run. So there is no
tool selection by intent anywhere in the domain layer. These are
**validated narrators**, not agents in the reasoning sense. Calling them
agents oversells them.

**Fact 2 — the one real reasoning agent is unwired.** `agents/investigator_agent.py`
(A2) is built to exactly the shape described in the question: a Strands
agent with real tools (`get_client_context`, `get_currency_exposure`,
`get_recent_balance_trend`), the LLM decides what to call, and the
result is a *proposal* recomputed deterministically before it can be
accepted. `grep "investigate("` outside its own module: **zero callers**.
Built, tested, never in a run path.

**Fact 3 — cross-domain linking is counting, not reasoning.**
`datainsights/correlation/hypothesis.py:184` — `domain_bonus = number of
distinct domains − 1`. Two signals from two domains earn +1 confidence.
Nothing asks whether the deposits signal *explains* the lending signal.
"Balance rising AND facility utilisation rising" (growth → financing)
and "balance rising AND utilisation flat" (surplus → treasury) are the
same category today, because only the strongest single signal picks it.

**Is the loop wrong?** No — for this system's governing constraint (the
LLM never decides category, sizing or score) a deterministic fan-out into
a deterministic fan-in is the correct *floor*, and it is what makes the
whole-book batch run cost nothing in LLM calls. The problem is that it is
also the *ceiling*: nothing sits above it that reasons.

**The production shape that reconciles both** — three tiers, each
already partially present:

| Tier | Role | Exists today? |
|---|---|---|
| **1. Deterministic scan** | Every detector, every domain, every client. Batch-scale, no LLM. The floor. | Yes — this is `evaluate_book()`. Keep it. |
| **2. Investigator** | ONE tool-using agent per *shortlisted* client, run *after* tier 1. Sees all domains' evidence plus the exogenous check; can call further lookups across concepts (the "multiple lookups / ETL-style joins" in the question); proposes a cross-domain hypothesis and a refined category. Its output is a **proposal** that deterministic predicates validate — it can never change the category on its own. | Built (A2), unwired. Promote it. |
| **3. Propensity ranker** | Supervised ML over tier-1/2 outputs as features and RM outcomes as labels; ranks the shortlist by likelihood of engagement. | Plumbing only (SLOT E4, `label_pipeline.py`, `model_registry.py`). Blocked on label volume — correctly. |

Plus one cheap, deterministic addition between tiers 1 and 2: a
**cross-domain rules table** in config for the *known* combinations
(deposits↑ + utilisation↑ → FINANCING_NEED; deposits↑ + utilisation flat
→ TREASURY_OPPORTUNITY; rating↓ + anything → RISK_REVIEW). Explainable to
model risk, covers the common cases without an LLM, and gives the
investigator a smaller residue of genuinely ambiguous cases to reason
about.

**On the ML question specifically** ("ML needs dependent and independent
variables and a prediction — am I right?"): yes, for supervised ML. Here
the independent variables are the tier-1 signals, their magnitudes, the
client context, and the cross-domain combinations; the dependent variable
is the RM outcome already being captured in `var/rm_feedback.db`
(Customer Engaged / Not Appropriate / Remind Me Later); the prediction is
propensity to engage. That is tier 3, and it is where ML genuinely earns
its place — **ranking** which suggestion to surface first. It is not
where suggestions are *generated*; that stays rules (tier 1) plus
investigation (tier 2). Today's revenue figures are rule-based sizing
from `config/rules.yaml`, and that is the correct state until labels
exist. The per-entity "challenger" in SLOT E2 is not this — see §3.

**Refactor R9 — wire the investigator as tier 2.** Call `investigate()`
from `evaluate_client()` for every recommendation with `ambiguous=True`
*or* more than one confirming domain; surface its proposal on the
recommendation as a separate, clearly-labelled field; the dashboard's
Trace tab shows it as "Stage 4b — investigation". Acceptance: the
existing live A2 test passes in the real run path, and a recommendation
with two domains carries an investigator note explaining how they
relate. **Refactor R10 — cross-domain rules table**: `config/domains_*.yaml`
gains a `combinations:` block; `assemble()` consults it before falling
back to strongest-signal-wins. Acceptance: the two examples above produce
different categories from the same strongest signal.

## 7. What not to do

- **Do not rebuild the correlation layer or the DataSource ABC.** Both are
  right. The temptation after an audit like this is a rewrite; the
  findings above are about *the layers between* two sound ones.
- **Do not introduce a workflow/agent framework** (this was asked before,
  M16). Nothing in §5 needs one; the coupling is in vocabulary and
  config placement, not orchestration.
- **Do not widen the semantic model without domain packs.** Adding
  concepts ad hoc to a flat list recreates the "7 constants" problem at
  15 constants. The pack is what makes "any tables" honest.
- **Do not claim ML on this data.** See §3. The plumbing is worth keeping;
  the claim is not worth making until E4 has labels.
