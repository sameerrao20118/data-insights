# Generalization plan — any data model, any event type, local → AgentCore

**Status: Phase 0 DONE and verified; Phase 1 DONE and verified,
including the detector/tool/qualifier rewire and a second real schema
(`config/bindings/legacy.yaml`) proving genericity; Phase 2 DONE and
verified IN FULL, including a second real event type (`fx_rate_move`)
proving qualification is genuinely declarative AND that same type's
extraction agent generalization (verified live: a real `fx_rate_move`
extracted from free text, zero Python edits beyond the registry/schema
work). Phase 3 has one narrow, verified slice DONE (the local-Ollama/
AgentCore call-path equivalence proof -- `agents/entrypoint.py`'s
`invoke()` and `agentcore_entrypoint()` are provably the same function,
plus a new `fdm_agentcore` profile that validates and honestly blocks
at its first real cloud dependency); its S3/Glue/DynamoDB adapters,
telemetry, and any actual deployment remain NOT started, as do
Phases 4-5. Written 2026-09-16 after a code survey of the repo as it
stood (M8 complete: domain registry, SLOT E2 opt-in, agentic phases
A0–A4); updated across the sessions that executed Phase 0, 1, 2 (core
and, on 2026-09-17, its extraction generalization), and the Phase 3
slice -- see the status note under each phase heading below, and
`docs/changelog.md`'s M9 section for full measured results (real
bugs found and fixed while verifying against real data, disclosed
there, not smoothed over). Every "today" claim below cites the file it
was verified in. This document is written to be executed by another
model or engineer phase by phase — each phase has files, interfaces,
acceptance tests, and an explicit NOT RUN list.

Read first: `CLAUDE.md` (hard boundaries — they all still apply),
`docs/architecture.md` (current design), `docs/decision_record.md` Tab 6
(the extension-slot intent this plan finally delivers on).

---

## 0. The four requirements, restated as testable claims

| # | Requirement | What "done" means, mechanically |
|---|---|---|
| R1 | **Any endogenous data model.** FDM is one example; a new schema must not require touching detectors, agents, correlation, or sinks. | All registered detectors run unchanged against a *second, deliberately different* schema through a config binding alone. A startup check fails fast if a binding doesn't cover what a registered detector needs. A test greps `detection_engine/`, `agents/`, `datainsights/correlation/`, `datainsights/sinks/` for physical FDM column names and fails if any remain. |
| R2 | **Any exogenous event type, user-registrable.** Exchange rates today, tenders tomorrow, anything next. | A new event type is registered in YAML (payload schema + matching rules + exposure rule + category hint) with zero Python edits; the A1 extraction agent and the exposure qualifier pick it up. Proven by registering `fx_rate_move` and running the whole book. |
| R3 | **Local MacBook ↔ AgentCore on AWS; Snowflake and S3/Glue at minimum.** Not an immediate AgentCore deployment, but nothing should have to be restructured to get there. | One profile-driven `build_runtime()` composes source, event source, model, state, and sinks. The same client yields a byte-identical `Recommendation` via (a) local CSV, (b) an S3/parquet-style source over a local parquet export, (c) `docker run` of the AgentCore entrypoint. Snowflake and Glue/Athena adapters pass the same conformance suite once credentials exist; until then they are contract + mock, NOT RUN — stated, not hidden. |
| R4 | **Self-service onboarding.** Bring a schema (DDL/CSV/Excel) or an exogenous source (CSV/Excel/JSON/text/endpoint); the system proposes the mapping, a person confirms, synthetic data fills gaps with explicit labels. | Phase 5's acceptance: the legacy schema onboarded from files matches the hand-written binding; an unfamiliar schema onboards end to end with synthetic fill and every derived row labelled. |

Plus one standing requirement: **ML / GenAI / agentic use stays
industry-standard and honest** — deterministic decisions, models where
they measurably help, agents that propose and explain but never decide,
evaluation harnesses that run in CI, traceability by construction.

---

## 1. Current-state findings (verified in code, 2026-09-16)

### 1.1 What is already generic (keep, build on)

- `datainsights/sources/base.py` — `DataSource` ABC is schema-agnostic:
  `read_entity(entity, start_date, end_date, columns)` + `capabilities()`
  + `BatchProvenance`. Nothing FDM-specific here.
- `config/entities_fdm.yaml` — a real logical contract (grain, PK,
  typed columns, bi-temporal flags, provenance labels). The *pattern* is
  right; only its *vocabulary* is FDM.
- Domain registry (M7): `config/domains_fdm.yaml` +
  `datainsights/domain_registry.py` (data) and `agents/domain_registry.py`
  (code). Adding a domain is one YAML block + one `register()` call —
  proven with Risk. This stays the way domains are added.
- `detection_engine/signal.py` `Signal` — the canonical cross-domain
  contract (`prty_id`, `signal_type`, `domain`, `direction`, `magnitude`
  0..1, `observed_date`, `evidence_ref`, `raw_measure`). Already
  schema-agnostic; correlation only ever sees this.
- `datainsights/correlation/` — reads `Signal`s and the domain registry.
  No physical column names. Generic already.
- `datainsights/sinks/` + `tests/test_sink_contract.py` — render one
  `Recommendation`; no source coupling except `fdm_worklist._client_context`
  (see 1.2).
- `agents/model_factory.get_model()` — the single provider swap point;
  `mode="model_gateway"` is a documented `NotImplementedError`.
- `agents/entrypoint.py` — AgentCore `@app.entrypoint` shape exists;
  `Dockerfile` exists (never built).
- `datainsights/agent_trace.py`, `datainsights/rm_feedback.py` — audit
  trail and feedback capture keyed on `recommendation_id`.
- `datainsights/ml/slots.py` — the four E-slot Protocols (E1 normaliser,
  E2 baseline, E3 exposure qualifier, E4 propensity) with deterministic
  defaults; `baselines.py` has the E2 challenger, measured.

### 1.2 Where FDM vocabulary leaks (the R1 work)

| Location | What's hard-coded | Evidence |
|---|---|---|
| Every detector's `REQUIRED_COLUMNS` | Physical FDM columns: `AGRMNT_DLY_BAL_STRT_DTTM`, `AGRMNT_LDGR_BAL_AMT`, `FIN_EVNT_PSTD_DT`, `FIN_EVNT_AMT`, `FIN_EVNT_SBTYP_CD`, `AGRMNT_CLOSE_DT`, `MORT_FXED_RT_END_DT`, `RSK_GRD_CD/VAL`, `PRTY_MTR_TYP_CD/VAL`, `CLTRL_*` | `detection_engine/*.py` lines 16–31 in each |
| `agents/tools.py` | Data slicing by FDM names and codes: `AGRMNT_TYP_CD`, `AGRMNT_ID_TRN_ACCT` → `AGRMNT_ID` rename, `"PARTY_AGREEMENT"`, `"MORTGAGE_AGREEMENT"`, `"AGREEMENT_COLLATERAL_ITEM"`, `"COLLATERAL_ITEM_VALUE"`, `"PARTY"` | `agents/tools.py:36-46, 56-121, 126-228, 266-300` |
| `datainsights/sources/fdm_local.py` | FDM-named convenience methods `party()`, `agreement()`, `daily_balance()`, `financial_event()`, `party_demographic()`, `party_locator()`, `collateral_item_value()`; callers depend on these, not on `read_entity` | `fdm_local.py:229-273` |
| `external_events/exposure_qualifier.py` | `NACE_SECTION_CD`, `COUNTRY_CD`, `PARTY_AGREEMENT`, `AGRMNT_TYP_CD == "DEP"`, `AGRMNT_ID_TRN_ACCT`; `EXPOSURE_CHECKS` is a Python dict with one entry; `ExogenousEvent` has fixed fields | `exposure_qualifier.py:43-51, 54-112, 108-110` |
| `external_events/event_extraction_agent.py` | `KNOWN_EVENT_TYPES = {"public_tender_award"}`; `ExtractedEventFields` is a fixed pydantic model | `event_extraction_agent.py:49, 55-63` |
| `datainsights/fdm_worklist.py` | `_client_context` reads `PRTY_SGMNT_CD`, `SECTOR_NM`, `COUNTRY_CD`; signature typed to `FdmLocalSource` | `fdm_worklist.py:147-157` |
| `agents/orchestrator.py` | `_high_risk_flag` reads `HIGH_RSK_CUST_IND == "Y"` | `orchestrator.py:77-82` |
| `agents/investigator_agent.py` | tools read `PRTY_SGMNT_CD`, `NACE_SECTION_CD`, `COUNTRY_CD`, `FIN_EVNT_CURY_CD`, `AGRMNT_TYP_CD`, `AGRMNT_LDGR_BAL_AMT` | `investigator_agent.py:70-128` |
| `Signal.prty_id`, `Recommendation.prty_id` | The name is FDM's, but it's already used as an abstract "party key" everywhere; keep the field, document it as canonical | `signal.py:25`, `hypothesis.py:44` |

### 1.3 Where local-only / hand-wired assumptions leak (the R3 work)

| Location | Assumption | Evidence |
|---|---|---|
| Six entry points construct `FdmLocalSource(FDM_DIR, CONTRACT_PATH)` directly | No profile-driven source selection for the FDM path at all | `agents/demo_fdm_scenario.py:60`, `agents/demo_multiagent_scenario.py:65`, `agents/entrypoint.py:75`, `datainsights/ml/compare_baselines.py`, `datainsights/ml/scale_evaluation.py`, `external_events/ingest_notices.py`, `dashboard/app.py` |
| `datainsights/config.py` | `SourceConfig.backend: Literal["offline_local","snowflake"]`; `StateConfig.backend: Literal["sqlite"]`; `OutputConfig.backend: Literal["local"]`; `LLMConfig.provider: Literal["ollama"]`; `RuntimeConfig.target: Literal["local"]`. Profiles exist (`config/profiles/*.yaml`) but only the legacy pipeline reads them. | `config.py:31-118` |
| `agents/entrypoint.py` | Still uses a stale `TOOL_FACTORIES = {"deposits", "lending"}` dict instead of the M7 registry (Risk/Exogenous missing); takes a filesystem `data_dir` in the payload | `entrypoint.py:35, 63, 68, 75` |
| State and artifacts | SQLite files and local dirs: `var/agent_traces.db`, `var/rm_feedback.db`, `var/insights/`, `var/ml_runs/`, `external_events/output_fdm_extracted/` | `agent_trace.py`, `rm_feedback.py`, `extracted_event_store.py`, demos |
| No S3/Glue/Athena source exists | `datainsights/sources/` has local, fdm_local, snowflake (NOT RUN), fdm_snowflake (NOT RUN) only | `ls datainsights/sources/` |
| No event-source abstraction on the FDM path | `load_events(path)` / `read_extracted_events(path)` are plain functions returning `list[ExogenousEvent]` by convention; the legacy `ExternalEventSource` ABC exists but is unused by the FDM pipeline | `exposure_qualifier.py:135`, `extracted_event_store.py`, `datainsights/sources/external_event_source.py` |
| Dockerfile | Never built or run; `CMD` documented as unverified | `Dockerfile` header |

### 1.4 ML / GenAI / agentic — where we are vs. industry practice

| Practice | Status | Gap |
|---|---|---|
| Point-in-time-correct features (Feast-style as-of joins) | As-at joins exist in `FdmLocalSource.as_at()` and every detector's own loop; **no shared feature layer** — each detector re-derives its own history slice | Duplicated PIT logic; E2/E4 can't reuse features |
| Structured outputs + code validation for every LLM call | Done (narrator, A1, A2, A3) | A2 lacks a reasoning-vs-evidence consistency check (disclosed bug) |
| Prompt versioning recorded in traces | `AgentTrace` records model label + latency; **prompts are inline f-strings with no version id** | Can't attribute a behaviour change to a prompt change |
| Golden evaluation sets, run in CI | A1 has 10 notices (live-gated); narrator has stubbed-model tests; **no per-agent eval harness with metrics tracked over time** | No regression signal for extraction/investigation quality |
| Observability (OpenTelemetry) | Custom SQLite trace | AgentCore Observability expects OTEL; no exporter |
| Tool contracts hostable by a gateway (MCP) | Strands `@tool` functions | AgentCore Gateway speaks MCP; no MCP surface |
| Model registry / model cards | E2 is stateless by design (no artifact); run manifests exist (`var/ml_runs`) | Nothing to register yet — correct. E4 needs a registry the day a label exists |
| Label pipeline for supervised learning | `rm_feedback` captures the taxonomy | No join to PIT features → training table; correctly gated (D6) |
| No ranker / arbitration | Correct by design (D6) | Keep |

---

## 2. Target architecture

```
                         ┌──────────────── config/profiles/<profile>.yaml ────────────────┐
                         │ runtime.target: local | agentcore                                │
                         │ source: offline_local | snowflake | s3_parquet | glue_athena     │
                         │ binding: fdm | legacy | <new schema>                             │
                         │ event_source: csv | s3 | snowflake                                │
                         │ llm: ollama | model_gateway (gated)   state: sqlite | dynamodb    │
                         └──────────────────────────────┬───────────────────────────────────┘
                                                        │ build_runtime()
   Physical schemas                Canonical layer      ▼                 Domain logic (schema-free)
 ┌──────────────────┐   ┌──────────────────────────────────────┐   ┌──────────────────────────────┐
 │ FDM (CSV/Snowfl.)│──▶│ config/bindings/fdm.yaml             │   │ detectors  (canonical cols)  │
 │ legacy (CSV)     │──▶│ config/bindings/legacy.yaml          │──▶│ features   (PIT-correct)     │
 │ <new schema>     │──▶│ config/bindings/<new>.yaml           │   │ tools / agents / correlation │
 │ (any DataSource) │   │ datainsights/semantic/CanonicalSource│   │ sinks                        │
 └──────────────────┘   │  concepts: Party, Account, Balance,  │   └──────────────┬───────────────┘
                        │  Transaction, RiskGradeVersion, ...  │                  │ Recommendation
                        └──────────────────────────────────────┘                  ▼
   External world                Event registry                              RM worklist / MIMO /
 ┌──────────────────┐   ┌──────────────────────────────────────┐             Pega mock / dashboard
 │ notices, feeds   │──▶│ config/event_types.yaml              │
 │ (any EventSource)│   │  payload schema · matching · exposure│──▶ exposure qualification (rule engine
 │ A1 extraction    │   │  rule · category hint · sizing basis │      + named check library)
 └──────────────────┘   └──────────────────────────────────────┘
```

Two ideas carry the whole plan:

1. **A canonical semantic model with per-schema bindings** (R1). Domain
   logic depends on *concepts* (`Account.balance`, `Transaction.amount`,
   `RiskGradeVersion.grade_value`), never physical columns. A binding is
   YAML that says how a given schema provides each concept — renames,
   value maps, join recipes, which fields are bi-temporal. This is the
   same idea as a dbt semantic layer / entity model, kept to one YAML
   file per schema and one Python class. FDM becomes *one binding*; the
   legacy schema already in the repo becomes the *second* binding — the
   proof of generality with no invented data.
2. **A declarative event-type registry** (R2). An event type is data:
   payload field schema, matching predicates over canonical `Party`
   attributes, an exposure rule composed from a small named-check
   library, and category/hypothesis/sizing hints. The extraction agent,
   the qualifier, and the correlation hints all read it. Bespoke Python
   exposure checks remain possible through a registry, exactly like
   `DomainSpec`.

Everything else (profiles, `build_runtime`, S3/parquet source, state
stores, MCP surface, OTEL) is plumbing that lets the same code run in
both places.

---

## 3. Phases

Order is deliberate: Phase 0 is small and removes the hand-wiring that
every later phase would otherwise have to work around; Phase 1 is the
core and the largest; Phases 2–4 build on a stable base. **Do not start
Phase 1 until Phase 0's acceptance passes.**

### Phase 0 — Runtime factory and profile-driven composition (small, first) -- **DONE**

**Goal**: one place composes source / event source / model / state /
sinks from a profile. No entry point constructs `FdmLocalSource` by
hand. AgentCore entrypoint reads its profile from the environment.

Files:
- `datainsights/config.py` — widen the literals:
  `RuntimeConfig.target: local | agentcore`;
  `SourceConfig.backend: offline_local | snowflake | s3_parquet | glue_athena`
  with `binding: str` (new, e.g. `fdm`) and `contract_ref`;
  new `EventSourceConfig(backend: csv | s3 | snowflake, path/ref)`;
  `StateConfig.backend: sqlite | dynamodb`; `OutputConfig.backend: local | s3`;
  `LLMConfig.provider: ollama | model_gateway`. Keep every existing
  validator (no `-cloud`, localhost only, no paid flags) — a
  `model_gateway`/`dynamodb`/`s3` selection must be *constructible* but
  the adapters raise `NotImplementedError` with the governance reason
  until Phase 3 (and, for the gateway, until explicitly authorized).
- `config/profiles/fdm_local.yaml` (new; current de-facto defaults),
  `config/profiles/legacy_local.yaml` (Phase 1), `config/profiles/agentcore.yaml`
  (Phase 3, contract-only).
- `datainsights/runtime.py` (new): `@dataclass Runtime(source, event_source, model_factory, state, artifact_store, profile)` and `build_runtime(profile_name_or_path) -> Runtime`. Env override `DATAINSIGHTS_PROFILE`.
- Refactor the six entry points in §1.3 to `rt = build_runtime(...)`.
- `agents/entrypoint.py`: replace `TOOL_FACTORIES` with `agents.domain_registry.all_specs()`; drop `data_dir` from the payload (source comes from the profile); keep `prty_id`, `as_of`, `narrate`, `domain`.

Acceptance:
- `python -m agents.demo_fdm_scenario` output byte-identical to today
  (diff `var/insights/fdm_rm_worklist.csv` minus timestamp columns).
- `tests/test_runtime.py`: builds `fdm_local`; asserts `s3_parquet`/`glue_athena`/`dynamodb`/`model_gateway` selections construct and raise `NotImplementedError` naming the phase/gate that unblocks them; asserts every existing config validator still fires.
- `grep -rn "FdmLocalSource(" agents/ datainsights/ml/ external_events/ dashboard/` returns only `datainsights/runtime.py`.
- Full suite green; both demos and Streamlit `AppTest` re-run.

NOT RUN after this phase: nothing new — no cloud adapter executes.

### Phase 1 — Canonical semantic model + bindings (the core of R1) -- **DONE**

**Design decision made during execution, differs from the sketch
below**: rather than renaming the nine detectors' `REQUIRED_COLUMNS` to
canonical names (touching every detector file and its hand-built
fixtures), the detectors and their existing unit tests were left
completely untouched. The FDM physical vocabulary they already expect
(`PRTY_ID`, `AGRMNT_ID`, `AGRMNT_LDGR_BAL_AMT`, `FIN_EVNT_PSTD_DT`, ...)
stays as the stable target; only the DATA-FETCHING layer
(`agents/tools.py`, `external_events/exposure_qualifier.py`,
`agents/orchestrator.py`, `datainsights/fdm_worklist.py`,
`agents/investigator_agent.py`, `agents/entrypoint.py`) now reads
through `CanonicalSource` and translates canonical → FDM-vocabulary
before handing data to a detector. This achieves the same goal (a new
schema needs only a new binding YAML, never a code change) with a
smaller, lower-risk diff. See `docs/changelog.md`'s M9 section for
the full account, including the second-schema proof.

**A third schema, added 2026-09-17, with REAL data**: `config/bindings/sba.yaml`
maps the U.S. Small Business Administration's real, public PPP loan-level
release (`data_generator/external/fetch_sba.py` — 968,524 real disbursed
commercial loans downloaded and verified byte-for-byte against the
source's reported file size) onto the same canonical concepts, with
synthetic deposit-account activity layered on top of a 400-entity sample
(`data_generator/fdm/load_sba.py`) because no public source discloses
real transaction history for any commercial client anywhere — a
confidentiality constraint on the whole data category, not a gap in this
build. Real borrower names, real NAICS industry sector codes (24 distinct
sectors in the sample — construction, healthcare, professional services,
manufacturing, retail, hospitality, and more), real loan amounts/dates/
outcomes. `tests/test_sba_binding_end_to_end.py` (6 tests) proves the
same 9 detectors, zero code changes, run against it, including an
assertion on real sector diversity (not an assumed list). Required one
small, disclosed addition to Phase 0's runtime factory: a new
`offline_local_flat` source backend (`datainsights/runtime.py`,
`datainsights/config.py`) alongside the existing `offline_local`, because
`FdmLocalSource` unconditionally expects bi-temporal
`EFFECTIVE_START_DT`/`EFFECTIVE_END_DT` columns this flat schema doesn't
have — `OfflineLocalSource` (already proven against `legacy`) doesn't. A
Berka (Czech retail banking, PKDD'99) alternative was investigated and
deliberately rejected: real data, but retail/personal, not commercial —
wrong fit for a platform whose value proposition is being sector- and
schema-agnostic across commercial clients specifically.

**Goal**: detectors, tools, exposure, worklist, orchestrator, and
investigator depend only on canonical concept/field names. FDM is one
binding; the legacy schema is the second; a third can be added by
writing YAML.

**1a. Define the canonical model** — `config/semantic_model.yaml`

Concepts and canonical fields (start from what the nine detectors and
the qualifier actually consume; do not over-model):

```yaml
concepts:
  Party:              # key: party_id
    fields: {party_id: str, segment: str, sector_code: str, country_code: str,
             high_risk_flag: bool, sector_name: str}
    bitemporal: optional          # binding says whether versions exist
  Account:            # key: account_id
    fields: {account_id: str, party_id: str, product_class: enum[deposit, facility, mortgage, other],
             product_code: str, original_limit: float?, opened_at: date?, close_date: date?,
             fixed_rate_end_date: date?, currency: str}
  BalanceObservation: # grain: account_id x observed_at
    fields: {account_id: str, observed_at: date, balance: float}
  Transaction:        # grain: one row per posted transaction
    fields: {account_id: str, posted_at: date, amount: float, direction: enum[credit, debit], currency: str}
  RiskGradeVersion:   # bi-temporal versions per party
    fields: {party_id: str, valid_from: date, valid_to: date?, grade_code: str, grade_value: int}
  PartyMetricVersion: # bi-temporal, e.g. PD_1Y
    fields: {party_id: str, valid_from: date, valid_to: date?, metric_type: str, value: float}
  CollateralValuation:
    fields: {account_id: str, collateral_id: str, valued_at: date, value: float, original_limit: float}
```

**1b. Bindings** — `config/bindings/fdm.yaml`, `config/bindings/legacy.yaml`

A binding maps each concept to a physical entity in a `DataSource`
contract, with renames, value maps, derived fields, and join recipes:

```yaml
schema: fdm
contract_ref: config/entities_fdm.yaml
concepts:
  Party:
    entity: PARTY
    bitemporal: {valid_from: EFFECTIVE_START_DT, valid_to: EFFECTIVE_END_DT}
    fields: {party_id: PRTY_ID, segment: PRTY_SGMNT_CD}
    derived:
      high_risk_flag: {from: HIGH_RSK_CUST_IND, map: {"Y": true, "N": false}}
    joins:                       # attributes living on other entities
      - {entity: PARTY_DEMOGRAPHIC, on: PRTY_ID, fields: {sector_code: NACE_SECTION_CD, sector_name: SECTOR_NM}}
      - {entity: PARTY_LOCATOR,    on: PRTY_ID, fields: {country_code: COUNTRY_CD}}
  Account:
    entity: AGREEMENT
    bitemporal: {valid_from: EFFECTIVE_START_DT, valid_to: EFFECTIVE_END_DT}
    fields: {account_id: AGRMNT_ID, product_code: AGRMNT_TYP_CD, original_limit: AGRMNT_ORIG_LIM, close_date: AGRMNT_CLOSE_DT}
    derived:
      product_class: {from: AGRMNT_TYP_CD, map: {DEP: deposit, LON: facility, ODR: facility, MTG: mortgage}, default: other}
      currency: {const: EUR}
    joins:
      - {entity: PARTY_AGREEMENT,    on: AGRMNT_ID, fields: {party_id: PRTY_ID}}
      - {entity: MORTGAGE_AGREEMENT, on: AGRMNT_ID, fields: {fixed_rate_end_date: MORT_FXED_RT_END_DT}, optional: true}
  BalanceObservation:
    entity: AGREEMENT_DAILY_BALANCE
    fields: {account_id: AGRMNT_ID, observed_at: AGRMNT_DLY_BAL_STRT_DTTM, balance: AGRMNT_LDGR_BAL_AMT}
  Transaction:
    entity: EVENT_FINANCIAL
    fields: {account_id: AGRMNT_ID_TRN_ACCT, posted_at: FIN_EVNT_PSTD_DT, amount: FIN_EVNT_AMT, currency: FIN_EVNT_CURY_CD}
    derived:
      direction: {from: FIN_EVNT_SBTYP_CD, map: {CRD: credit, DBT: debit}}
  RiskGradeVersion:
    entity: PARTY
    bitemporal: {valid_from: EFFECTIVE_START_DT, valid_to: EFFECTIVE_END_DT}
    fields: {party_id: PRTY_ID, grade_code: RSK_GRD_CD, grade_value: RSK_GRD_VAL}
  CollateralValuation:
    entity: COLLATERAL_ITEM_VALUE
    fields: {collateral_id: CLTRL_ITEM_ID, valued_at: EFFECTIVE_START_DT, value: CLTRL_VAL_AMT}
    joins:
      - {entity: AGREEMENT_COLLATERAL_ITEM, on: CLTRL_ITEM_ID, fields: {account_id: AGRMNT_ID}}
      - {entity: AGREEMENT, on: AGRMNT_ID, fields: {original_limit: AGRMNT_ORIG_LIM}}
  PartyMetricVersion: {unavailable: "PARTY_METRIC not in contract -- no DDL captured"}
```

`config/bindings/legacy.yaml` binds the existing `config/entities.yaml`
schema (`clients`/`accounts`/`transactions`/`balances`, already
generated under `data_generator/output/`). Concepts it cannot provide
(`RiskGradeVersion`, `CollateralValuation`, `fixed_rate_end_date`) are
declared `unavailable` with a reason — detectors that need them are
skipped **at startup, with a logged reason**, never at runtime.

**1c. `CanonicalSource`** — `datainsights/semantic/canonical.py`

```python
class CanonicalSource:
    def __init__(self, source: DataSource, binding: Binding, semantic_model: SemanticModel): ...
    def available(self, concept: str) -> bool
    def read(self, concept: str, *, as_at: date | None = None,
             start: date | None = None, end: date | None = None,
             party_id: str | None = None, account_id: str | None = None) -> pd.DataFrame
        # returns canonical column names only; applies renames, value maps,
        # consts, joins; applies the as-at predicate when the binding marks
        # the concept bitemporal and as_at is given; otherwise returns versions
    def versions(self, concept: str, *, party_id: str) -> pd.DataFrame   # full history, canonical
    def provenance(self) -> BatchProvenance
```

Plus `datainsights/semantic/binding.py` (pydantic models for the YAML,
`load_binding(path)`), and `datainsights/semantic/validate.py`:
`validate_binding(binding, semantic_model, contract) -> list[str]` —
every mapped physical column must exist in the contract; every required
canonical field must be provided or the concept marked unavailable.

**1d. Detector requirement declarations**

Each detector module gains
`REQUIRES = {"BalanceObservation": ["account_id", "observed_at", "balance"], "Account": ["party_id", "product_class"]}`
and its `REQUIRED_COLUMNS` become canonical names. `detect()` signatures
stay as they are (they receive DataFrames) — only the column names
change, so the existing hand-built-fixture tests need a mechanical
rename, not a rewrite. `agents/domain_registry.DomainSpec` gains
`requires: dict[str, list[str]]` (union of its detectors'); the
orchestrator calls `validate_domains_against(canonical_source)` at
startup and skips (with a logged, surfaced reason) any domain whose
concepts are unavailable under the active binding.

**1e. Rewire consumers to `CanonicalSource`**

- `agents/tools.py`: `_party_agreements` → `canonical.read("Account", as_at=..., party_id=...)`
  filtered on `product_class`; balance/transaction slices via
  `read("BalanceObservation"/"Transaction", account_id=..., start=..., end=...)`;
  collateral via `read("CollateralValuation", ...)`; risk via
  `versions("RiskGradeVersion", party_id=...)`. Product codes in
  `config/domains_fdm.yaml` become **product classes** (`deposit`,
  `facility`, `mortgage`) — the binding owns the physical codes.
- `agents/orchestrator._high_risk_flag` → `Party.high_risk_flag`.
- `datainsights/fdm_worklist._client_context` → `Party` canonical fields;
  drop the `FdmLocalSource` type hints.
- `agents/investigator_agent.py` tools → canonical reads.
- `external_events/exposure_qualifier.py` matching → canonical `Party`
  fields (the rest of the qualifier is Phase 2).
- `FdmLocalSource`'s FDM-named methods: keep for one release, mark
  deprecated in their docstrings; nothing in the pipeline may call them.
- Rename `datainsights/fdm_worklist.py` → `datainsights/worklist_rm.py`
  only if cheap; otherwise leave the file name and fix the docstring.

Acceptance (all met, verified this session):
- **Regression oracle**: `agents/demo_fdm_scenario.py` and
  `agents/demo_multiagent_scenario.py` re-run end to end after the
  rewire and reproduce the same recommendations, sizing, and
  positive/negative exposure proof as before it (the multiagent demo's
  real Ollama narration included). Streamlit dashboard verified via
  `AppTest`, no exception.
- **Second binding, proven**: `config/bindings/legacy.yaml` maps the
  pre-existing legacy schema (`config/entities.yaml`,
  `data_generator/output/`) onto the same seven canonical concepts.
  `tests/test_legacy_binding_end_to_end.py` runs `make_deposits_tools`/
  `make_lending_tools` — the same functions, zero edits — against real
  legacy data: deposits/lending tools detect real signals, and
  risk-domain tools report `not_available_under_this_binding` rather
  than crash (that data isn't contracted in the legacy schema). No
  Python edits were needed beyond Phase 1 itself to add this binding.
- `tests/test_semantic_bindings.py`: both bindings validate against
  their contracts; an unavailable concept makes `available()` false and
  call sites (`check_collateral_coverage`, `check_rating_downgrade`,
  sector/geography matching, the high-risk-flag check) degrade to that
  reason instead of crashing.
- `tests/test_no_source_specific_coupling.py`: the mechanical guard
  that keeps R1 true — none of the five rewired call sites may
  construct `FdmLocalSource` directly or hardcode an FDM physical
  entity/table name; the only physical vocabulary they may reference is
  the FDM-style rename target, and only as a literal dict, not tied to
  any one `DataSource` implementation.
- Existing detector unit tests: unchanged, still pass (the detectors
  themselves were never edited — see the design-decision note above).
- Full suite: **320 passed** (was 296 before this session's work; 315
  after Phase 0 + the semantic-model foundation alone).

### Phase 2 — Declarative event-type registry + `EventSource` (R2) -- **DONE, including extraction generalization**

**Goal**: an exogenous event type is registered in YAML; extraction,
matching, exposure, and category hints all read it.

**What actually got built, and how it differs from the sketch below**:
`config/event_types.yaml` + `external_events/event_registry.py` (typed,
load-time-validated access) + `external_events/exposure_checks.py`
(the named-check library: `has_account`, `recent_signal`,
`currency_activity`, plus `register_exposure_check()` for bespoke
Python) together drive `external_events/exposure_qualifier.py`'s
`qualifies()` generically -- neither it nor
`datainsights/correlation/hypothesis.py` has an `if event_type ==
"public_tender_award"` branch any more. `datainsights/sources/
event_source.py`'s `EventSource` ABC + `CsvEventSource` formalizes what
`load_events()` already did by convention. A second event type,
`fx_rate_move`, is registered in YAML only and proven end to end
(`tests/test_fx_exposure_end_to_end.py`) against real FDM-shaped data
(a temp-materialized copy of the generated dataset with a few
real-shaped USD transaction rows added -- disclosed in that test's own
docstring, not touching the checked-in dataset so every other test's
byte-identical assumptions stay intact).

**Extraction generalization, closed 2026-09-17**: `external_events/
event_extraction_agent.py` no longer has a schema hardcoded for
`public_tender_award`. `config/event_types.yaml` gained an
`extraction:` block per type (`core_fields` for ExogenousEvent's fixed
fields, plus `pattern`/`enum`/`min`/`max`/`min_exclusive`/
`grounded_in_quote`/`currency`/`optional` metadata on both core and
`payload` fields); `_build_fields_model(event_type)` builds a pydantic
model via `create_model` from that schema, `_build_extraction_prompt()`
generates the system prompt from it, and `_validate()` is one generic
function driven by the same field metadata -- no per-type branch left
anywhere in the module. Extraction is two LLM calls, not one:
`_classify_event_type()` (which registered type, if any, does this
text describe?) then a type-specific extraction call using that type's
own dynamic model.

**Real live-Ollama finding, disclosed rather than hidden**: a bare
`tools=[]` Agent asking for a small structured-output schema
intermittently emitted NO tool call at all with this local model
(qwen2.5:7b via ollama) -- not a design bug, a genuine reliability quirk
of forcing small-schema structured output with no other tools
registered (`strands`' own "ToolChoice ... not supported" warning is
the proximate cause: ollama doesn't actually enforce the forced
tool-choice, so an empty response is possible). Fixed by registering
one harmless placeholder tool (`_noop`) on both agents -- verified
stable across three consecutive live runs after the fix (was failing
on most runs before it). A one-retry fallback in
`_classify_event_type` remains as a second line of defense.

**Verified, not just built**: `tests/test_live_extraction_precision_recall_on_golden_notices`
now spans BOTH event types (12 notices: 6 tender, 2 fx, 4 negative
cases) and passed live with **TP=7, FP=0, FN=1, TN=4** -- a real
`fx_rate_move` extraction from free text ("The EUR/USD pair fell 6%
against the dollar...") producing a correctly-typed, correctly-grounded
`ExogenousEvent` with `payload={currency_pair, currency, pct_change,
window_days}`, no Python edits beyond the registry/schema work above.
`test_live_classification_picks_the_right_type_for_each_source_text`
verifies the classify stage picks the right type for each. All 21
deterministic tests (both types' field-format checks, including the
percent-as-fraction grounding a live run's own quote wording required:
"fell 6%" grounding `pct_change=-0.06`) still pass. The acceptance
bullet below ("A1 extracts fx_rate_move with zero Python edits") is now
MET.

Files:
- `config/event_types.yaml` (new):

```yaml
public_tender_award:
  description: "Public-sector tender awarded to a company in a sector/country"
  source: {name: TED, real_source_type: "TED (Tenders Electronic Daily) API", extraction_needed: false}
  payload:                      # typed fields the ExogenousEvent carries
    estimated_value_eur: {type: float, required: true, must_be_grounded_in_quote: true}
    severity: {type: int, min: 1, max: 5}
  match:                        # predicates over canonical Party fields
    - {field: sector_code, equals: "$event.affected_sector", wildcard_if_empty: true}
    - {field: country_code, equals: "$event.affected_country", wildcard_if_empty: true}
  exposure:                     # composed from the named-check library, or a registered python check
    all_of:
      - {check: has_account, product_class: deposit}
      - {check: recent_signal, signal_type: revenue_pattern_change, within_days: 365}
    magnitude: {from_check: recent_signal, field: change_pct, clamp: [0, 1]}
  correlation:
    category_hint: FINANCING_NEED
    hypothesis: "Winning a public tender creates a cash-flow gap between delivery and payment..."
    sizing: {basis: pct_of_event_value, pct: 0.25, label: "25pct_of_event_value_illustrative"}

fx_rate_move:                   # the second type -- the proof for R2
  description: "A material move in an exchange rate pair over a short window"
  source: {name: ECB SDMX, real_source_type: "ECB exchange-rate series", extraction_needed: false}
  payload:
    currency_pair: {type: str, required: true, pattern: "^[A-Z]{3}/[A-Z]{3}$"}
    pct_change: {type: float, required: true}
    window_days: {type: int, default: 30}
    severity: {type: int, min: 1, max: 5}
  match:
    - {field: country_code, in: "$event.affected_countries", wildcard_if_empty: true}
  exposure:
    all_of:
      - {check: currency_activity, currency: "$event.payload.currency_pair[3:]", within_days: 180, min_transactions: 3}
    magnitude: {from_payload: pct_change, abs: true, clamp: [0, 1]}
  correlation:
    category_hint: HEDGING_NEED
    hypothesis: "A sharp move in a currency the client transacts in changes the value of their open exposure..."
    sizing: {basis: not_sized_no_exposure_amount_on_file, label: "not_sized_this_pass"}
```

- `external_events/event_registry.py` (new): loads/validates the YAML;
  builds a pydantic payload model per type (used by A1 for structured
  output and by validation); `known_event_types()`, `spec(event_type)`.
- `external_events/exposure_qualifier.py`: `ExogenousEvent` becomes
  `{event_id, event_type, event_date, source_name, source_ref, affected_sector, affected_country, affected_countries: list, payload: dict}`;
  `qualifies()` evaluates `match` predicates against canonical `Party`,
  then the `exposure` composition via a **named-check library**
  (`external_events/exposure_checks.py`: `has_account`, `recent_signal`,
  `currency_activity`, `balance_trend`, …, each a small function over
  `CanonicalSource`), plus `register_exposure_check(name, fn)` for
  bespoke Python (the `DomainSpec` pattern). `EXPOSURE_CHECKS` dict
  removed.
- `datainsights/correlation/hypothesis.py`: exogenous category/
  hypothesis/sizing come from the event type's `correlation` block, not
  the hard-coded `public_tender_award` branch (`hypothesis.py:216-247`).
  Sizing bases become a small library keyed by `sizing.basis`.
- `external_events/event_extraction_agent.py`: `KNOWN_EVENT_TYPES` and
  `ExtractedEventFields` come from the registry (dynamic pydantic model:
  common fields + the type's payload fields); `_validate` applies the
  payload constraints (`must_be_grounded_in_quote`, patterns, ranges).
- `datainsights/sources/event_source.py` (new): `EventSource` ABC
  mirroring `DataSource` — `read_events(*, start, end, event_types) -> (DataFrame, EventBatchProvenance)`,
  `capabilities()`; implementations `CsvEventSource` (the fixture and
  the A1 store share it), `SnowflakeEventSource` and `S3EventSource`
  (Phase 3). `tests/test_event_source_conformance.py` mirrors the
  DataSource conformance test. Retire the legacy
  `datainsights/sources/external_event_source.py` by pointing its one
  caller at the new ABC or leaving it untouched in the legacy appendix —
  do not maintain two.
- Fixture: `external_events/output_fdm/fx_events.csv` with one
  `fx_rate_move` (EUR/USD −6% over 30 days) — built, but
  `data_generator/fdm/generate_fdm.py` itself was NOT changed (the
  sketch here proposed a generator flag adding USD transactions;
  actually done instead: `tests/test_fx_exposure_end_to_end.py`
  copies the real generated directory to a temp path and adds a few
  real-shaped USD rows there, so every other test's byte-identical
  assumption about the checked-in dataset stays completely
  untouched — a smaller, equally real, lower-risk proof).

Acceptance (met unless noted):
- **Met.** Tender proof unchanged (PRTY00036 positive, PRTY00037
  negative), verified via `agents/demo_fdm_scenario.py` re-run producing
  byte-identical output, with the tender type now driven entirely from
  `event_types.yaml` (`tests/test_no_source_specific_coupling.py`-style
  discipline: no `"public_tender_award"` string comparison remains in
  `qualifies()` or `assemble()`).
- **Met, adjusted scope.** `fx_rate_move` registered in YAML only;
  `tests/test_fx_exposure_end_to_end.py` proves a party with real
  (test-injected, disclosed) USD transaction activity qualifies and one
  without does not, with the YAML hypothesis and a magnitude derived
  from `payload.pct_change` -- not run against the whole 60-party book
  (the checked-in dataset has no USD activity by design; see the
  design-decision note above), and this pass's registered category
  comes from whichever endogenous signal (e.g. `fixed_rate_expiry`,
  already ambiguous between `TREASURY_OPPORTUNITY`/`HEDGING_NEED`) the
  exogenous confirmation attaches to, not a `category_hint` mechanism
  (not built -- unneeded for this proof, real future work if a type
  needs to force a category with no natural endogenous signal at all).
- **Met (2026-09-17).** A1 extracts an `fx_rate_move` from a
  hand-written notice with zero Python edits beyond the registry/schema
  generalization work described above -- verified live, TP=7 FP=0 FN=1
  TN=4 across a 12-notice golden set spanning both event types. See the
  design-decision note above for the dynamic-model/prompt/validation
  mechanism and the real local-model tool-calling reliability fix it
  required.
- **Met.** `tests/test_event_registry.py`: unknown check name, a
  `correlation` block missing `hypothesis`, a `magnitude.from_check` not
  in the type's own `exposure.all_of`, and a match predicate missing
  `field` all fail validation at load time with a message naming the
  type/field. `tests/test_event_source_conformance.py` (7 tests) covers
  the `EventSource` ABC.

### Phase 3 — Sources, stores, and the AgentCore path (R3) -- **one slice DONE (local↔AgentCore call-path equivalence); everything below still NOT started**

**Goal**: same code, two runtimes. Local proves everything that can be
proven without credentials; cloud adapters are contract + mock + the
same conformance suite, marked NOT RUN until they run.

**What's actually verified so far** (2026-09-17, in response to "for
all the implementations, two methods: run it locally on Ollama, and run
it at AgentCore"): `agents/entrypoint.py`'s `agentcore_entrypoint` is
provably the SAME function `invoke()` is — `BedrockAgentCoreApp
.entrypoint()`'s own source (installed package, verified by reading it)
registers the function unchanged and returns it as-is, so calling
`agentcore_entrypoint(payload)` starts no server and makes no network
call. `tests/test_entrypoint.py::test_agentcore_entrypoint_produces_identical_output_to_invoke`
asserts byte-identical output for the same payload; a second test
asserts `app.handlers["main"] is agentcore_entrypoint` (the real
registration, not an assumption about it). New
`config/profiles/fdm_agentcore.yaml` is the AgentCore-shaped
counterpart to `fdm_local.yaml` -- it validates and constructs as far
as honestly possible, then raises `NotImplementedError` naming the
exact first blocking reason (`s3_parquet` source has no adapter and no
credentials), never a silent local fallback
(`test_fdm_agentcore_profile_validates_and_constructs_as_far_as_honestly_possible`).
This is real, narrow, verified work -- it is NOT the S3/Glue/DynamoDB
adapters, telemetry, or an actual deployment described below, all of
which remain exactly as NOT STARTED as before.

Files:
- `datainsights/sources/s3_parquet_source.py` (new): a `DataSource`
  over DuckDB `httpfs` — reads `s3://bucket/prefix/{domain}/{table}.parquet`
  **or** a local `file://` parquet tree with the identical code path.
  Same contract (`entities_fdm.yaml`), same FinCrime refusal, same
  provenance. Locally testable: a small script exports the CSV dataset
  to parquet under `data_generator/output_fdm_parquet/`.
- `datainsights/sources/glue_athena_source.py` (new): contract + mock
  (`boto3`/`pyathena` optional import; constructor validates config;
  `read_entity` raises `NotImplementedError("NOT RUN: needs AWS credentials + Glue catalog")`).
- `FdmSnowflakeSource`: unchanged, but now covered by the binding
  conformance suite so drift is detectable the day it runs.
- `datainsights/state/` (new package): `StateStore` ABC (`agent_traces`,
  `rm_feedback`, `run_manifests` collections) with `SqliteStateStore`
  (today's three files consolidated) and `DynamoDBStateStore` (contract
  + mock); `ArtifactStore` ABC (`put/get/list` for worklist CSV, digest,
  MIMO JSON, extracted events, review queue) with `LocalArtifactStore`
  and `S3ArtifactStore` (contract + mock). `build_runtime()` wires them.
  `agent_trace.py`, `rm_feedback.py`, `extracted_event_store.py`,
  `fdm_worklist.write_*`, `mimo_placeholder.write_insights`,
  `scale_evaluation.write_run_manifest` write through the stores.
- Observability: `datainsights/telemetry.py` — OpenTelemetry spans
  around `evaluate_client`, each agent call, each tool call, each
  `CanonicalSource.read`; local exporter = console/SQLite (feeds
  `AgentTrace`), AgentCore = OTLP exporter selected by profile.
  `opentelemetry-sdk` is on the bank Artifactory list.
- MCP surface (contract): `agents/mcp_server.py` exposing the registered
  tools (read-only) over MCP so AgentCore Gateway can host them; run
  locally with the `mcp` package (on Artifactory) to prove the contract;
  AgentCore Gateway registration NOT RUN.
- `agents/entrypoint.py`: profile from `DATAINSIGHTS_PROFILE`; add
  `healthcheck()`; `/invocations` payload validated with pydantic.
- `Dockerfile`: make it actually build and run locally against the
  `fdm_local` profile with the dataset mounted read-only — no Ollama in
  the container; `narrate=false` path proves the container; `narrate=true`
  reaches the host's Ollama via `host.docker.internal` for a local-only
  smoke, never a cloud model.
- `config/profiles/agentcore.yaml`: `runtime.target: agentcore`,
  `source.backend: s3_parquet`, `event_source.backend: s3`,
  `state.backend: dynamodb`, `output.backend: s3`, `llm.provider: model_gateway`
  — loads and validates; every adapter raises its NOT RUN reason.
- `docs/deployment.md` (new): the exact AgentCore steps (container
  build/push, Runtime create, Gateway tool registration, Memory,
  Identity, Observability), each marked NOT RUN with the authorization it
  needs, plus the local equivalents that *are* run.

Acceptance:
- `tests/test_source_conformance_matrix.py`: for each available source
  (`FdmLocalSource` CSV, `S3ParquetSource` over local parquet), every
  canonical concept read returns identical frames; Snowflake/Glue entries
  are `xfail(reason="NOT RUN: credentials")`, not skipped silently.
- **Three-runtime oracle**: the same `prty_id`/`as_of` yields a
  byte-identical `Recommendation` via (a) `fdm_local` profile, (b) an
  `s3_parquet_local` profile over the parquet export, (c) `docker run`
  of the image invoking `entrypoint.invoke` with `narrate=false`.
- `tests/test_state_stores.py`: Sqlite/Local stores round-trip; DynamoDB/
  S3 stores construct and raise NOT RUN.
- OTEL: one local run produces spans; `AgentTrace` rows are still
  written (they become a span exporter, not a second system).
- MCP: `python -m agents.mcp_server` lists tools; a local MCP client
  calls `check_cash_buildup` and gets the same dict as the direct call.

NOT RUN after this phase (and say so in `docs/current_state.md`):
AgentCore Runtime/Gateway/Memory deployment, Model Gateway inference,
Snowflake and Glue/Athena reads, DynamoDB/S3 stores. All contract-and-mock.

**Deployment sequencing, confirmed 2026-09-17**: real AWS deployment --
the Terraform/IaC enablement (a separate `infra/terraform/` module:
S3 buckets, Glue catalog, DynamoDB table, IAM roles, the AgentCore
Runtime resource itself) and the target-account connection method (an
IAM role ARN/profile read from the `fdm_agentcore` profile or
environment, a config concern, never hardcoded) -- comes LAST, after
Phase 3's remaining adapters (as contract+mock, same as everything
above), Phase 4, and Phase 5 are all proven locally. Writing Terraform
is safe under `CLAUDE.md`'s boundary the same way the Python adapters
are: authoring `.tf` files defines infrastructure, it doesn't deploy
it; `terraform plan`/`apply` against a real account is the actual
boundary, gated behind explicit authorization exactly like
`s3_parquet_source.py`'s real credentials are. Track this as a new,
explicit "Phase 6 -- Terraform + real AWS deployment" when work on it
starts, rather than folding it into this phase's already-large scope.

### Phase 4 — ML, GenAI, and agentic practice to industry standard -- **DONE**

**Goal**: the honest, standard scaffolding — PIT features, evals in CI,
prompt versioning, the A2 consistency fix, an E4-ready label pipeline —
without building anything the boundaries forbid.

**A2 consistency fix, done and verified live 2026-09-17**: exactly the
bug this section already anticipated. `config/domains_fdm.yaml`'s
`category_options` for `fixed_rate_expiry` is now a mapping
(`TREASURY_OPPORTUNITY: {requires_evidence: balance_rising}`,
`HEDGING_NEED: {requires_evidence: non_eur_currency_activity}`), read
via `datainsights/domain_registry.py`'s new
`category_evidence_requirements()`. `agents/investigator_agent.py`'s
three tool bodies were extracted into plain `_get_*` functions so
`investigate()` can recompute the SAME facts directly in Python
(`EVIDENCE_PREDICATES`) rather than trusting the model's paraphrase of
what it saw; `_validate()` rejects any proposal whose predicate is
false, to `needs_review`, driven by data not a prompt tweak.

Real bug caught, not hypothetical: a live run against PRTY00001
proposed `HEDGING_NEED` with reasoning stating "no multi-currency
activity found" -- self-contradicting (absence of FX exposure is
`TREASURY_OPPORTUNITY` evidence). Re-ran the live test **3 times after
the fix**: the model proposed `HEDGING_NEED` every time (a real,
repeatable bias in this local model for this client's data, not a
one-off), and the new rule correctly rejected it to `needs_review`
every time, citing the exact missing evidence. 7 new deterministic
tests in `tests/test_investigator_agent.py` cover the predicate logic
directly (no Ollama needed); the live test now asserts that IF a
rejection happens, it's for this real reason, not a coincidental
failure.

- **Feature layer, DONE**: `datainsights/features.py`'s `compute(series,
  as_of=..., date_col=..., value_col=..., window_days=..., agg=...)` --
  ONE PIT-safe windowed aggregation (mean/median/sum/count/last/first),
  `None` (never a fabricated 0) when nothing falls in the window.
  `tests/test_features_point_in_time.py` (8 tests) proves the property
  that matters: a row dated after `as_of` never changes an earlier
  result. Deliberately NOT a detector migration -- the 9 existing
  detectors already implement their own correct as-of filtering, each
  independently proven; this is the shared primitive available to future
  consumers (SLOT E4, Phase 5's onboarding tool), not a retrofit of code
  that already works.
- **SLOT E2, already satisfied, no new code needed**: checked
  `datainsights/ml/baselines.py`'s `IsolationForestBaseline` against the
  plan's own ask -- it already takes an arbitrary
  `dict[(entity_id, metric_key), history]`, not a hardcoded metric, and
  `datainsights/ml/evaluate_baselines.py` already runs a controlled
  injection sweep against real data with hyperparameters recorded in
  every run manifest. Generalizing something already generic would be
  manufactured work; documented as satisfied instead.
- **SLOT E4 readiness (no model), DONE**: `datainsights/ml/label_pipeline.py`
  joins `rm_feedback` → `recommendation_id` → the worklist row's own
  features (PIT-correct by construction -- a worklist row is never
  recomputed retroactively) into a training table with a fixed schema.
  Run live against this session's own dashboard testing: found ONE real
  `Customer Engaged` feedback row already recorded, joined correctly.
  `datainsights/ml/model_registry.py` -- `var/models/<name>/<version>/
  {model.joblib, card.json}`, refuses to save without a complete
  `ModelCard` (purpose, data window, features, metrics, limitations,
  owner). 9 tests (`tests/test_label_pipeline_and_registry.py`). Still no
  ranker (D6) -- registering one is not this repo's call.
- **Prompt versioning, DONE**: `prompts/*.yaml` (`domain_agent`,
  `investigator_agent`, `rm_copilot_agent`, `event_extraction_classify`)
  with `version:`; `datainsights/prompts.py`'s `load_prompt(name)` is the
  loader every agent now calls instead of an inline f-string --
  byte-verified identical to the pre-refactor prompt text for all four.
  `AgentTrace` gained `prompt_version` (with an in-place sqlite migration
  for a pre-existing local `var/agent_traces.db`, verified against this
  session's own db file); a live demo run confirmed it's actually
  recorded. `tests/test_prompt_versioning.py`'s hash check
  (`prompts/hashes.json`) is the real mechanism -- verified it actually
  catches drift by editing a template's text without bumping `version`
  and watching the test fail, then restoring it and watching it pass.
  The event-extraction agent's dynamic per-type EXTRACT prompt (built
  from `config/event_types.yaml`'s own schema, Phase 2) is intentionally
  NOT double-versioned here -- that schema's `contract_version` already
  is its version.
- **Evaluation harness in CI, DONE**: `tests/eval/test_golden_sets.py`
  under a new `pytest -m eval` marker (`pytest.ini`), separate from the
  regular suite the same way every other live-gated test already is
  (named `..._live_...`, so `-k "not live"` excludes them exactly as
  before). Three golden sets, run live: **A1** (event extraction, reusing
  the 12-notice set) TP=7 FP=0 FN=1 TN=4; **A2** (investigator
  evidence-consistency) 3/3 runs upheld; **A3** (RM copilot, a NEW golden
  set that didn't exist before -- 6 grounded/ungrounded Q&A pairs against
  a real worklist row) all correctly answered or honestly declined.
  Metrics written to `var/eval/<run_id>_<golden_set>.json` per run.
- **A2 consistency fix** -- **DONE**, see above.
- **Guardrail tests, DONE**: `tests/test_guardrails.py` (22 tests).
  Parametrized red-team sweep -- every one of `BANNED_TERMS` (not just
  the one example term each agent's own test already covered) rejected
  by all four LLM-facing agents. The "no label access" check is a static
  source sweep, not a monkeypatched `open()` -- `OfflineLocalSource`/
  `FdmLocalSource` read CSVs through DuckDB's own C-level file I/O
  (`read_csv_auto(...)`), which never calls Python's `open()` at all, so
  a monkeypatch would have given false confidence. Confirmed
  `protected_evaluator_only`/`trigger_events` appear only in the
  generator that writes it, the dedicated evaluator, the refusal checks
  themselves, and disclaimer comments -- never in code that could
  actually read data at runtime.
- Optional retrieval over bank policy docs: not built, per this
  section's own instruction not to build speculatively -- no use case
  has appeared.

Acceptance: eval harness runs locally end to end and produces metrics --
**MET**. **A2 golden set has 0 evidence-contradicting proposals accepted
-- MET** (7 deterministic evidence-predicate tests plus a live test
verified 3/3 runs). **PIT feature test passes -- MET**. **Prompt-hash
test passes -- MET**, and verified to actually catch drift, not just
exist. **Label pipeline dry-run produces a training table with the
documented schema -- MET**, run against real captured feedback, not a
fixture. Full suite: **419 passed** (was 372 before this phase).

---

### Phase 5 — Self-service onboarding utility ("drop your schema here") -- **5a (schema onboarding, CSV input) DONE; 5b/5c/5d NOT started**

**What was actually built, 2026-09-17, and how it differs from the plan
below**: the CSV-directory schema-onboarding slice, end to end,
including the LLM binding proposer -- not just the deterministic
profiler. `onboarding/profiler.py` (deterministic: columns, inferred
types, key candidates, bi-temporal pair detection -- verified against
real FDM data, correctly found `agreement`'s
`EFFECTIVE_START_DT`/`EFFECTIVE_END_DT` pair); `onboarding/binding_proposer.py`
(local Ollama, one small structured-output call per canonical concept,
never trusting a proposed entity/column that isn't real -- rejected
mappings are logged, not silently dropped);
`onboarding/entity_contract_generator.py` (deterministic contract
generation); `onboarding/proposal_writer.py` (renders a
`config/bindings/<name>.yaml`-shaped proposal + a human-readable review
report with confidence/evidence/rejections per mapping);
`onboarding/accept.py` (the mandatory human gate -- validates via the
SAME `validate_binding()` every hand-written binding is held to,
refuses to overwrite an existing schema without `--force`, writes
nothing on any validation failure). A new "Onboard" dashboard tab
(profile+propose → review/edit the proposed YAML inline → accept) makes
it demoable, not just scriptable.

**Real, live, blind-test proof, not a fixture**: ran the actual proposer
against the real SBA data (`data_generator/output_fdm_sba/`) with NO
hint that a hand-built "right answer" (`config/bindings/sba.yaml`)
already existed. Result: `BalanceObservation` and `Transaction` --
unambiguous straight column-name matches in the real data -- came back
exactly right, confidence 1.00 both. `RiskGradeVersion` and
`PartyMetricVersion` came back OVER-CONFIDENT and wrong (proposing
`opened_at` as a risk grade's `valid_from`, a party's `legal_name` as a
`PartyMetricVersion.value` -- plausible-sounding, semantically false).
This is not a bug to paper over -- it is the exact, real demonstration
of why "human confirmation is mandatory" is this phase's own stated
design, not boilerplate caution.
`tests/test_onboarding_live.py::test_live_onboarding_end_to_end_against_real_sba_data`
asserts the two unambiguous concepts, edits the two risky ones to
`unavailable` (the human-review step, done in the test), accepts, and
runs the accepted binding's `BalanceObservation`/`Transaction` reads
through `CanonicalSource` for real -- proving the whole chain, not just
the LLM call. 15 deterministic tests (`tests/test_onboarding.py`) cover
the profiler, contract generator, proposal writer, and every branch of
`accept()`'s validation gate (rejects an invalid proposal and writes
nothing; refuses to silently overwrite; `--force` allows a deliberate
overwrite) without needing Ollama.

**Purely additive, verified structurally**: a new `onboarding/` package
plus one new dashboard tab -- zero changes to any existing detector,
agent, correlation, or `DataSource` file.
`tests/test_onboarding.py::test_onboarding_never_touches_real_config_directory`
asserts `accept()` never writes outside the `repo_root` it's explicitly
given; every other onboarding test runs against a `tmp_path`, never the
real `config/` directory, so nothing in this phase's own test suite
could have mutated an existing schema even by accident.

**Scoped out, disclosed, not silently assumed**:
- **5b** (schema-driven synthetic data generation, writing "through the
  binding in reverse" into the user's physical shape) -- not built.
  Presence assessment and canonical-level synthetic generation remain
  real, undone work.
- **5c** (exogenous event onboarding: tabular/text/endpoint → a proposed
  `config/event_types.yaml` entry) -- not built. Phase 2's declarative
  event registry is the engine this would sit on; nothing wires an LLM
  proposer to it yet.
- **DDL text and Excel data dictionaries** -- not built. CSV directories
  are what every schema onboarded to this platform so far (`fdm`,
  `legacy`, `sba`) has actually looked like, so that's the one input
  shape this pass builds and proves end to end, rather than three
  half-built readers.
- **The endpoint catalog and `FetchAdapter` contract** -- not built;
  correctly blocked on the same "no external call without explicit
  authorization" boundary as Phase 3's real cloud adapters.
- `python -m onboarding.run --profile <name>` -- not built as a
  separate entry point; redundant with what already exists --
  `agents/demo_fdm_scenario.py --profile <name>` already runs any
  accepted schema's profile, onboarding or not.

Full suite: **434 passed** (was 419 after Phase 4).

**Goal** (the product target, restated): a user brings a data model —
DDL, a folder of CSVs, an Excel data dictionary — and the system
*proposes* how it maps to the canonical model, checks whether data is
present, generates clearly-labelled synthetic data where it isn't, and
runs the pipeline. Same for exogenous: bring an endpoint description or
a CSV/Excel/JSON/text file, and the system proposes an event type,
registers it, and extracts events. Output is unchanged: hypotheses with
sized actions for commercial/institutional clients. Phases 1–2 build
the engine this sits on; do not start Phase 5 before both pass.

**5a. Schema onboarding agent** — `onboarding/schema_onboarding.py`
- Inputs: SQL DDL text, a directory of CSV/parquet files (headers +
  sample rows), or an Excel data dictionary (`table, column, type,
  description`).
- Profiler (deterministic): tables, columns, inferred types, key
  candidates, date columns, cardinalities, bi-temporal pairs
  (`*_START_DT`/`*_END_DT`-like), sample values with PII-safe truncation.
- **Binding proposer (LLM, local Ollama, structured output)**: given the
  profile and `config/semantic_model.yaml`, propose a `config/bindings/<name>.yaml`
  — concept → entity, field renames, value maps, joins, bitemporal
  markers, and an `unavailable:` list with reasons. Every proposed
  mapping carries a confidence and the evidence (column name, sample
  values) it was based on.
- Validation (deterministic): `validate_binding()` from Phase 1 against
  the generated contract; type compatibility; join keys exist; a
  dry-run `CanonicalSource.read()` per concept.
- **Human confirmation is mandatory**: the proposal is written to
  `onboarding/proposals/<name>/binding.proposed.yaml` + a review
  report; nothing is registered until a person runs
  `python -m onboarding.accept <name>` (or clicks Accept on the
  dashboard's new "Onboard" page). The A2 rule applies: the agent
  proposes, code validates, a person decides.
- Also generates the `config/entities_<name>.yaml` contract from the
  profile (grain/PK/columns), with `provenance: profiled_from_user_schema`.

**5b. Data-presence assessment + schema-driven synthetic generation** —
`onboarding/data_presence.py`, `data_generator/canonical/`
- Presence check per concept: `absent` (not in binding), `empty`
  (bound, zero rows), `thin` (below each registered detector's minimum
  history — reuse `min_observations`/`window_days` from `config/rules.yaml`),
  `sufficient`. Surfaced as a table before any run.
- **Canonical synthetic generator**: generates `Party`, `Account`,
  `BalanceObservation`, `Transaction`, `RiskGradeVersion`, … at the
  canonical level with the same seeded scenario injection the FDM
  generator does today (buildups, step changes, downgrades, dormancy),
  then writes them **through the binding in reverse** into the user's
  physical table shapes (renames/value maps inverted; joins re-split).
  `generate_fdm.py` becomes the FDM instance of this, not a special case
  — keep its output byte-identical via a regression test.
- Fill policy is explicit and per concept: `real_only`, `synthetic_fill_empty`,
  `synthetic_fill_thin`. Every synthetic row carries
  `__provenance = synthetic:<seed>` and every sink/digest row derived
  from any synthetic input is labelled `synthetic-derived` — the
  "illustrative" discipline extended to data, so a mechanism proof is
  never read as a business result.
- Labels for evaluation stay under `protected_evaluator_only/`
  exactly as today; the detector path never sees them.

**5c. Exogenous onboarding** — `onboarding/event_onboarding.py`
- Inputs: a tabular file (CSV/Excel/JSON) *or* text documents *or* an
  endpoint description (URL + auth type + sample payload).
- Tabular: profile columns → **event-type proposer (LLM)** proposes a
  `config/event_types.yaml` entry (payload schema, match fields,
  exposure composition from the named-check library, category hint)
  plus a column→payload binding; validated by `event_registry`; human
  accepts. Excel/JSON readers are thin (`pandas`/`openpyxl`, both on the
  bank Artifactory list).
- Text: routes to A1 with the (proposed, accepted) event type.
- Endpoints: an **endpoint catalog** (`config/event_endpoints.yaml`:
  name, URL, auth, schedule, response→payload binding) with a
  `FetchAdapter` contract. **Any live fetch — even a free public API
  (ECB SDMX, TED) — is an external call under `CLAUDE.md` and stays
  contract + mock (recorded sample payloads) until the user authorizes
  it in writing.** Files are the real path locally.

**5d. Entry points**
- CLI: `python -m onboarding.schema <path-or-ddl> --name <schema>`,
  `python -m onboarding.events <file-or-endpoint-spec> --name <type>`,
  `python -m onboarding.accept <name>`, `python -m onboarding.run --profile <name>`.
- Dashboard: an "Onboard" page — upload/point, see the profile, the
  proposed binding with confidences and evidence, the presence table,
  Accept/Reject per mapping, then "Generate synthetic where missing"
  and "Run".

Acceptance (status as of 2026-09-17 — 5a done against a real dataset
instead of the legacy schema, honestly graded, 5b/5c not attempted):
- ~~Onboard the legacy schema from its CSV folder with no hand-written
  binding, graded against `config/bindings/legacy.yaml`.~~ **Not run
  against legacy** — instead run against a real, external dataset (SBA
  PPP loans) blind-tested against the hand-written
  `config/bindings/sba.yaml`, which is a strictly harder and more
  honest test (the model has never seen this schema's naming
  conventions, unlike `legacy`, which shares vocabulary with `fdm`).
  Result was NOT a clean pass: 2 of 4 concepts
  (`BalanceObservation`, `Transaction`) came back correct;
  2 (`RiskGradeVersion`, `PartyMetricVersion`) came back wrong with
  high apparent confidence and required a human reviewer to catch —
  exactly the failure mode this phase exists to guard against. **MET,
  differently and more rigorously than specified, not silently
  claimed as a clean pass.**
- Onboard a hand-made "core banking" DDL with alien naming
  (`cust`/`acct`/`txn`/`bal_hist`) → presence table → synthetic fill →
  `synthetic-derived` labelling: **NOT MET.** 5b (presence assessment +
  reverse-binding synthetic generation) was not built this pass; only
  CSV-shaped input with real or already-generated data was proven.
- Onboard an exogenous Excel file of FX moves → proposed
  `fx_rate_move` registry entry: **NOT MET.** 5c was not built.
- A deliberately ambiguous column yields a flagged low-confidence
  mapping, not a silent guess: **MET, found for real, not staged.**
  The live SBA run's `RiskGradeVersion`/`PartyMetricVersion` proposals
  ARE this case — the proposer did not flag them as low-confidence
  (a real, disclosed gap in `binding_proposer.py`'s self-assessment,
  not yet fixed), but `accept()`'s validation gate and the mandatory
  human-review step caught them regardless, which is what the
  end-to-end guarantee actually depends on.
- Every synthetic-derived output row is labelled: **N/A this pass** —
  no synthetic-derived rows were produced (5b not built).

Effort: ~2 weeks after Phases 1–2. Sequencing: 5a/5b need Phase 1;
5c needs Phase 2; 5d last. Actual effort this pass: 5a only, one
session, atop Phases 1–4 already complete.

## 4. Definition of done for "generic" (the checklist reviewers use)

1. A second binding (`legacy`) runs every provided detector with zero
   code changes; a third binding needs only YAML.
2. `tests/test_no_physical_names_leak.py` passes and is in CI.
3. `fx_rate_move` exists only as YAML + fixture and is qualified end to end.
4. The three-runtime oracle (local CSV / parquet / container) is
   byte-identical.
5. Every cloud adapter is constructible from a profile and states its
   NOT RUN reason; none silently falls back to local data.
6. No agent decides a category/score/size (unchanged); A2's proposals are
   evidence-consistent by validation.
7. `docs/current_state.md` separates verified / assumed / NOT RUN for
   every phase, with numbers from actual runs.

---

## 5. Guardrails for whoever executes this

- **Do not delete the legacy pipeline.** It becomes the second binding's
  data (`config/entities.yaml`, `data_generator/output/`) — it is now
  load-bearing for R1's proof.
- **Never read `protected_evaluator_only/`** from any new module, test
  fixture, or eval set. Golden sets are hand-written and say so.
- **No paid or cloud model calls.** `model_gateway`/Bedrock stay
  `NotImplementedError` until the user authorizes them in writing.
  `OLLAMA_NO_CLOUD=1`; `-cloud` tags remain rejected.
- **No real external writes** (S3, DynamoDB, MIMO, Pega, email). Mocks
  and contracts only.
- **Byte-identical regression oracle after every phase**: `demo_fdm_scenario`
  worklist and `compare_baselines` output must not change unless the
  phase explicitly intends it — and then the diff is explained in the
  phase report.
- **As-of correctness** is a test, not a comment: every new read path
  and feature has a "future row doesn't change the past" test.
- **One phase at a time; finish with**: full suite, both demos, Streamlit
  `AppTest` (including the copilot/feedback clicks), and a report that
  separates verified facts / assumptions / NOT RUN / executed results.
- **Measure before optimizing** (the SLOT E2 lesson: the first
  hypothesis about the bottleneck was wrong; a micro-benchmark found the
  real one).
- **Don't add frameworks without a gap they close**: no LangGraph, no
  Feast, no MLflow server, no vector DB unless a phase above names the
  need. Small typed interfaces + YAML, the pattern this repo already
  proves.

---

## 6. Effort and sequencing summary

| Phase | Scope | Rough effort | Unblocks |
|---|---|---|---|
| 0 | Runtime factory, profiles, entrypoint fix | 2–3 days | everything |
| 1 | Canonical model, two bindings, detector/tool/qualifier rewire, leak test | 1–2 weeks | R1 |
| 2 | Event registry, `EventSource`, `fx_rate_move`, A1 dynamic schema | ~1 week | R2 |
| 3 | S3/parquet source, Glue contract, state/artifact stores, OTEL, MCP, container run, agentcore profile | ~1 week local; cloud NOT RUN | R3 |
| 4 | Feature layer, eval harness, prompt versioning, A2 fix, E4 readiness | ~1 week | standards |
| 5 | Self-service onboarding: schema profiler + LLM-proposed bindings (human-accepted), presence check, canonical synthetic generator, exogenous file/endpoint onboarding, Onboard page | ~2 weeks | the product target |

Phases 2 and 3 can run in parallel after Phase 1. Phase 4's A2 fix and
prompt versioning can start any time after Phase 0.
