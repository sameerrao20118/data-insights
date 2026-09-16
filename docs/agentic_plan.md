# Making DataInsights genuinely agentic — plan and user journey

Status: **A0, A1, A2, A3, A4 built and verified (real Ollama, real
generated data, real Streamlit AppTest clicks); A5 correctly not
started -- gated on RM-response label access (D6), which this session's
own A3 feedback capture is the first real step toward, not a
substitute for.** A1 is also wired into `agents/demo_fdm_scenario.py`
now, not just built standalone. Written 2026-09-14 against the code as
it stood after M7 (domain registry, sink interface, SLOT E2 baseline
opt-in); updated 2026-09-15/16 as each phase executed. See
`docs/current_state.md`'s two "agentic plan execution" sections for full
measured results (real bugs found and fixed, real before/after numbers,
one honest correction of a false alarm) -- summarised inline below each
phase. Every phase names the existing artefact it reuses and the hard
boundary it must not cross.

**One open item, disclosed rather than hidden**: A2's live run surfaced
a real reasoning-consistency bug (a proposal contradicted its own stated
evidence) that current validation doesn't catch -- see A2's section
below. Treat its proposals as unconfirmed until that's fixed.

## 0. The honest starting point

Today the repo is a deterministic rules engine with an LLM writing the
summaries. `agents/` uses the Strands framework, but the agent never
chooses a tool (the prompt says "call every tool"), never plans, never
decides a category, and in whole-book batch mode is never invoked at all.
`DomainAgent.evaluate()` even runs every detector twice — once itself,
once via the model — and only trusts the first run.

That restraint is mostly right. `docs/decision_record.md` D4 says
detection stays deterministic and the LLM narrates verified facts; the
constraints list forbids an autonomous multi-step agent; AgentCore
governance (Tab 5) gets heavier the more an LLM decides. So the goal is
**not** "let an agent decide who gets a credit offer." The goal is to put
agents where they add capability the rules engine cannot have, and keep
every decision, number and category in tested deterministic code.

## 1. Design principle: agents investigate and explain; code decides

```
                 unstructured world            structured world
                ┌──────────────────┐        ┌─────────────────────────┐
  news, notices │ A1 Event         │ typed  │ exposure_qualifier      │
  filings, ---> │ Extraction Agent │ -----> │ detectors, correlation  │ ---> Recommendation
  tenders       │ (LLM, validated) │ event  │ (deterministic, tested) │        │
                └──────────────────┘        └─────────────────────────┘        │
                                                      ▲                        ▼
  RM question   ┌──────────────────┐   read-only      │      ┌──────────────────────────┐
  "why is this  │ A3 RM Copilot    │ tools scoped to  │      │ A2 Investigator Agent    │
  client here?" │ (LLM, cites      │ one client's <───┘      │ (LLM, proposes a         │
                │  evidence)       │ evidence                │  refinement, RM confirms)│
                └──────────────────┘                         └──────────────────────────┘
                                                      feedback (Customer Engaged / Not
                                                      Appropriate / Remind Me Later)
                                                      keyed on recommendation_id -> A5
```

Rules that hold in every phase:

- **Output of every agent is a typed record validated in code** — the
  `DomainAgent._validate` discipline (schema, numbers traceable to
  evidence, banned terms, template fallback) generalised, not
  re-invented per agent.
- **No agent writes a category, score, offer size or revenue figure.**
  Those come from `datainsights/correlation/hypothesis.py` and
  `datainsights/fdm_worklist.py` only. An agent may *propose* a
  refinement as a separate field that a person confirms.
- **Every agent run leaves a trace** (prompt version, model label,
  tools called, evidence hashes) stored against `recommendation_id` —
  the audit trail and lineage AgentCore Phase 2 asks for, built in from
  the start rather than retrofitted.
- **Local Ollama only** through `agents/model_factory.get_model()`;
  Model Gateway stays the documented Stage 3 swap. No FinCrime/PEP
  surface anywhere (`BANNED_TERMS`, `FdmLocalSource` refusals apply to
  agents as they do to detectors).
- **LLM cost is bounded by construction:** the whole book runs
  deterministically; agents run only on the shortlist an RM will
  actually open (the decision record's own "sample a handful, template
  the rest").

## 2. Phases

### A0 — Make the current agent honest (small, do first) -- **DONE**

- Remove the duplicate tool execution in `DomainAgent.evaluate()`: pass
  `_gather_tool_evidence()`'s facts to the model as grounding, drop the
  second tool pass. Same validated output, roughly half the latency of
  the one-client demo.
- Rename the story in `agents/README.md`: "deterministic pipeline,
  LLM narration" — then each phase below adds a real capability on top.
- Add the `AgentTrace` record (`datainsights/agent_trace.py`) and store
  it with every narration, keyed on `recommendation_id`.

Reuses: `domain_agent.py`, `model_label()`, `recommendation_id` (M7).
Gate: `test_orchestrator.py` still proves narrate=True/False give the
same Recommendation; new test proves a trace exists per narration.

**Executed:** tools removed from the Strands `Agent` call (it now
narrates the same verified-facts text block that grounded the old,
duplicate tool pass); `datainsights/agent_trace.py` built, wired into
`agents/orchestrator.py` via an optional `trace_db_path`. Measured: live
one-client demo **~24s → 16.7s**. `tests/test_agent_trace.py` (3 tests).
See `docs/current_state.md`'s "Agentic plan execution" section.

### A1 — Exogenous event extraction agent (the capability gap) -- **DONE**

The decision record's Gap 1 is that nothing in the estate triggers on an
external event. The one event source today (`tender_events.csv`) is
already structured; the sources that matter next — news, regulatory
notices, sector announcements, company filings — are not. This is the
one place an LLM does work a rule cannot.

- `agents/event_extraction_agent.py`: input = raw text + source
  metadata; output = `ExogenousEvent` (existing dataclass in
  `external_events/exposure_qualifier.py`: `event_type`,
  `affected_country`, `affected_sector` (NACE section), `severity`,
  `estimated_value_eur`, `event_date`) plus `source_ref`, `quote`
  (the sentence the value came from) and `confidence`.
- Validation in code: NACE section must be in the registry's allowed
  set, country ISO-2, date parseable and ≤ ingest date (as-of),
  `estimated_value_eur` must appear in `quote`; anything failing is
  written to a review queue, never to the events table.
- Output lands in a **versioned events table** behind a new
  `EventSource` interface (same shape as `DataSource`: local CSV/DuckDB
  now, Snowflake/Glue later), so `load_events()` callers don't change.
- **Exposure qualification stays deterministic** — `qualifies()` is
  untouched. The agent says "a tender was awarded in sector C in ES";
  the rules say which client is genuinely exposed.

Reuses: `ExogenousEvent`, `generate_fdm_events.py`'s fixture shape as
the golden test set, `DomainAgent` validation pattern, `model_factory`,
`config/domains_fdm.yaml` for the allowed sector codes.
Gate: extraction precision/recall on a hand-labelled set of ~50
synthetic notices (written by us, not generator labels); the existing
tender-award end-to-end test passes with the agent-extracted event
replacing the CSV row.

**Executed (built as `external_events/event_extraction_agent.py` +
`external_events/extracted_event_store.py`, not `agents/`, to sit beside
`exposure_qualifier.py`):** 10 hand-written notices, not 50 (scope
tradeoff, disclosed). **Correction to the bullet above**: no formal
`EventSource` interface (ABC/Protocol, multiple implementations, a
conformance test) was actually built — that overclaimed what exists.
What's real is `extracted_event_store.read_extracted_events()` and
`exposure_qualifier.load_events()` returning the identical
`list[ExogenousEvent]` shape, so one is a drop-in for the other today —
but there's no typed interface enforcing that, and no second
implementation (Snowflake/S3) to check it against. Formalizing this
(mirroring `DataSource`'s ABC + conformance-test pattern) is real,
undone work, not a detail — see the "extending to a new event TYPE"
gap in the answer to "can this extend" questions. Two real bugs found and fixed on the first two
live runs -- `event_type` wording drift (model invented "tender",
"contract_award", ...; fixed by listing the exact allowed string) and a
currency-mismatch false positive (a GBP notice's number was accepted as
EUR because "EUR" appeared elsewhere in the quote as a negation; fixed
with a proximity-based currency check). Final: **TP=5, FP=0, FN=1**, the
one miss a genuine model numeric slip validation correctly caught.
`tests/test_event_extraction_agent.py` (15 tests + 1 live),
`tests/test_extracted_event_store.py` (3 tests). Full detail in
`docs/current_state.md`.

### A2 — Investigator agent for ambiguous signals -- **DONE, one bug open**

Some signals are honestly ambiguous and the code currently hard-codes a
guess: `fixed_rate_expiry` → TREASURY_OPPORTUNITY "as the more common
case", `revenue_pattern_change` without a contract stays unsized.

- Runs only for a Recommendation flagged `ambiguous` by deterministic
  code (a new boolean on `Recommendation`, set from a small explicit
  list in `config/domains_fdm.yaml`).
- Tools are read-only, scoped to that client: `get_signals`,
  `get_agreement`, `get_balance_history`, `get_related_events`. It
  gathers evidence and returns an `InvestigationNote`: `proposed
  refinement` (e.g. HEDGING_NEED vs TREASURY_OPPORTUNITY), the evidence
  rows cited, and what it could not find.
- The note is attached to the Recommendation and shown to the RM as
  "suggested reading, unconfirmed." **Category does not change** unless
  the RM confirms in the dashboard — that confirmation is itself
  feedback (A5).

Reuses: agent tool registry (M7), `Signal.evidence_ref` for citations,
`fdm_worklist` digest rendering.
Gate: property test — for any input, the assembled category is
identical with and without the investigator; note text passes the
numeric-traceability check.

**Executed, built as `agents/investigator_agent.py` (three tools:
`get_client_context`, `get_currency_exposure`, `get_recent_balance_trend`
-- narrower than the plan's four, scoped to what `fixed_rate_expiry`'s
TREASURY_OPPORTUNITY vs HEDGING_NEED question actually needs), gated by
new `config/domains_fdm.yaml` fields (`ambiguous`, `category_options`)
+ `datainsights/domain_registry.py` accessors + `Recommendation.ambiguous`,
not a separate "ambiguous list."** No client in the 60-person book
currently has an active `fixed_rate_expiry` detection, so tested against
a hand-built Recommendation using a real client's other data. **Live run
found a real bug this plan's own validate-or-fallback discipline didn't
catch**: the model proposed HEDGING_NEED while its own stated evidence
said "no multi-currency activity found" -- a direct contradiction,
undetected because the category itself was validly in
`category_options` and validation never checked reasoning against
conclusion. Not fixed this pass -- needs a `_direction_problem`-style
consistency check (`agents/domain_agent.py` already has the pattern for
narrative direction). Treat A2's proposals as unconfirmed until that
lands. 9 tests (`tests/test_investigator_agent.py`, 1 live).

### A3 — RM copilot: "why is this client on my list?" -- **DONE**

- A chat panel on the Streamlit **FDM worklist** page, scoped to the
  selected `prty_id`. Tools: `get_recommendation`, `get_signals`,
  `explain_sizing` (returns `sizing_basis` and the arithmetic),
  `get_investigation_note`, `get_client_context` (segment/sector/country
  only). No tool can reach another client or any table outside the
  evidence already used.
- Every answer must cite `evidence_ref`s; an answer with none is
  replaced by the template ("I can only explain from the evidence on
  this recommendation").
- Same panel captures the RM's response using the live taxonomy verbatim
  — **Customer Engaged / Not Appropriate / Remind Me Later** with
  sub-reason — stored against `recommendation_id`. This is the D6
  feedback loop; in production it arrives from Pega→CRM instead, same
  schema.

Reuses: `dashboard/app.py` FDM worklist page, `Recommendation`,
`pega_event_mock`'s `response_actions`, narrative cache pattern
(evidence-hash keyed) so repeated questions don't re-call the model.
Gate: `AppTest` covers the panel; a scope test proves the copilot's
tools raise on any `prty_id` other than the selected one.

**Executed, narrower and safer than planned: no tools at all.**
`agents/rm_copilot_agent.py`'s context is ONE dict (the worklist row
already read for the "prepare for the client call" section) -- no
`DataSource` access, so there is structurally nothing else to leak,
tighter than A2's live-query tools. Wired into `dashboard/app.py`
directly. `RM_WORKLIST_COLUMNS` gained `recommendation_id` + `ambiguous`.
New `datainsights/rm_feedback.py` captures the live taxonomy against
`recommendation_id` -- the real start of D6, not a mock. **Verified via
Streamlit `AppTest` driving actual clicks**, not just page load: typed a
question, clicked Ask, got a live qwen2.5:7b answer grounded in the row,
no exception; selected "Customer Engaged," clicked Save, confirmed the
row landed in `var/rm_feedback.db`. One real bug found and fixed:
numeric-traceability validation only scanned numeric-typed row fields,
so a legitimate restatement of `sizing_basis`'s embedded "25pct" was
flagged as fabricated -- fixed to also scan string fields. 14 tests
(`tests/test_rm_feedback.py`, `tests/test_rm_copilot_agent.py`, 1 live).

### A4 — Domain onboarding agent (developer-facing) -- **rating_downgrade wired, DONE**

Your maintainability concern, addressed with the registry that now
exists. `docs/adding_a_new_domain.md` is already a precise checklist;
an agent can follow it.

- Input: a sanitised DDL/column extract for a new domain (Risk is the
  live candidate — `rating_downgrade.py` and `pd_migration.py` already
  exist, unwired, waiting on exactly this).
- Output, as a branch for review: `config/entities_fdm.yaml` entries,
  a detector following the `DetectorConfig`/`detect`/`apply_cooldown`/
  `to_signal` shape, hand-built tests in the existing style, the
  `config/domains_fdm.yaml` block, the `register(DomainSpec(...))` call,
  and a generator extension only if DDL exists (else a documented
  `NotImplementedError`, per the doc's own rule).
- Human reviews the PR; the existing suite and `test_domain_registry.py`
  are the acceptance bar. The agent never touches `protected_evaluator_only/`
  or label logic (`.claude/settings.json` deny rules apply to it).

Reuses: the whole M7 registry, `adding_a_new_domain.md`, existing
detector tests as few-shot examples.
Gate: first real use is wiring Risk from the two orphaned detectors.

**Executed, for `rating_downgrade` only:** not a standalone "onboarding
agent" component (that would be this very session's own coding-agent
loop -- circular to build separately) -- executed as the deliverable
that agent would produce: `agents/tools.py`'s `make_risk_tools` +
`config/domains_fdm.yaml`'s `risk:` block, registered like every other
domain. Zero edits to `orchestrator.py`/`domain_agent.py`/`hypothesis.py`.
Hit the exact "missing field breaks `to_signal()` silently" bug the doc
above warns about (`grade_effective_date` omitted from the tool's
evidence dict) -- fixed, one line, caught by the whole-book demo
crashing rather than a passing-but-wrong test. Verified on real data:
whole-book RISK_REVIEW **2 → 3**. `pd_migration` still unwired --
needs `PARTY_METRIC`, which doesn't exist; not invented.
`tests/test_risk_domain_wiring.py` (3 tests).

### A5 — Learning from feedback (gated, later)

Once RM responses exist (A3 locally, Pega→CRM in production) and access
is granted, SLOT E4 `PropensityModel` gets a real implementation trained
on that label, keyed by `recommendation_id`. Per D6 it **ranks nothing
and arbitrates nothing** — Pega adaptive models own that. It can inform
`signal_strength` only through the decomposable score, visibly.
Not started until the label is actually accessible; today's
`UnavailablePropensityModel` stays.

## 3. Scalability and future-readiness

| Concern | How the plan handles it |
|---|---|
| LLM cost at book scale | Deterministic batch for all clients; agents (A1 per event, A2/A3 per opened client) run on the shortlist only. Cache by evidence hash. |
| Thousands of clients | Detectors are per-agreement and parallelisable; the registry loop in `orchestrator.py` is the split point for multiprocessing or a Spark/Glue job later. SLOT E2's per-point refit cost (measured ~4s/agreement) must be cached before any ML baseline goes default. |
| New data sources | `DataSource` (FDM local → Snowflake → Glue/Iceberg) is a real ABC with a conformance test proving two implementations agree. Events (A1) are **not** at that rigor yet — `load_events()`/`read_extracted_events()` happen to return the same shape, with no interface enforcing it and no second implementation to check against. Real, undone work if a second event store (Snowflake/S3) is ever added — not a parity claim with `DataSource`. |
| New domains / use cases | A4 + registry: one YAML block, one `register()` call, one detector file. Shared files untouched (proven by test). |
| Model provider change | `model_factory.get_model()` is the single swap; Stage 3 Model Gateway relaxes the localhost validator as a governed decision. |
| Pega / MIMO delivery | Every agent output is a field on the one `Recommendation`; sinks render it (`test_sink_contract.py`). New attribute keys registered in `ATTRIBUTE_KEYS` before emission. |
| Governance (AIRA/AIDEA) | Trace per run, human-in-the-loop by construction, no decision made by a model, feedback capture from day one. |
| Evaluation without leakage | Agent quality is measured on hand-written notices and RM feedback, never on `protected_evaluator_only/`. |

## 3b. Technology choices — what runs what, and why

Every row below is anchored to something already verified: either
installed and imported in this repo, or confirmed on the bank's
Artifactory (`docs/decision_record.md` Tab 7). Where a choice is a
recommendation rather than a fact, it says so.

### Data: understanding and executing it

| Layer | Now (local, verified) | Bank / at scale | Why |
|---|---|---|---|
| Storage + query | CSV per FDM entity, read through **DuckDB** (`FdmLocalSource`) — DuckDB 1.5.5 confirmed on Artifactory | **Snowflake** (`FdmSnowflakeSource`, same interface, NOT RUN), later Glue/Iceberg/Athena | DuckDB gives Snowflake-like SQL and columnar speed on a laptop with zero infrastructure; the `DataSource` contract means the swap is config, not code (conformance test already proves the two sources can't drift). |
| In-memory compute | **pandas 3 + numpy** inside the detectors | Same code; for thousands of clients, per-agreement parallelism (multiprocessing) first, **Snowpark/Spark** only if needed | Detectors are per-agreement and embarrassingly parallel. Rewriting them in Spark before a real dataset demands it is the "framework without evidence" mistake CLAUDE.md warns against. |
| Schema/contract | `config/entities_fdm.yaml` + `config/domains_fdm.yaml` (YAML, validated in code) | Same files | One contract drives generator, sources, detectors, agents. |
| Events (A1) | CSV table of validated `ExogenousEvent`s, read by a plain function (`extracted_event_store.read_extracted_events()`), not a typed interface | Snowflake table, or S3+Athena | Shape matches `exposure_qualifier.load_events()` today, but nothing enforces that beyond convention — see the correction in §2's A1 section. |
| Profiling/exploration | DuckDB SQL + pandas in notebooks or the Streamlit **Data sources** page | Snowflake worksheets | Nothing extra to install. |

**Answer to "which tool understands the data":** DuckDB for querying,
pandas for transformation, the YAML contracts for meaning. No Spark, no
feature store, until a real dataset shows the need.

### ML: is Python alone enough?

**Yes, for everything in this plan.** Concretely:

| Need | Tool | Status |
|---|---|---|
| Client-specific baselines (SLOT E2) | `scikit-learn` IsolationForest, median/MAD in `statistics`/numpy | Built, measured (Step 2); needs fit-caching before default |
| Propensity from RM feedback (SLOT E4, A5) | scikit-learn logistic regression / gradient boosting; **calibrated, explainable** | Blocked on label access (D6) |
| Extraction quality (A1) | plain precision/recall on a hand-labelled set — no model training | Not started |
| Model persistence + versioning (Step 3) | `joblib` artefacts with `{model, rule_version, as_of, training_window, data_hash}` sidecar JSON; MLflow only if MMS registration asks for it | Not started |
| Experiment tracking | The repo's own convention: a `docs/` results table + the script that produced it, seed-pinned | In use |

Tab 7's measured machine (2 cores, no GPU, 32 GB) runs all of this
comfortably: sklearn on thousands of rows is seconds. What Python alone
is **not** enough for is the LLM itself — that needs the Ollama server
binary locally (a software request on the bank box) or the Model
Gateway at Stage 3. No deep-learning framework (PyTorch/TensorFlow) is
needed anywhere in this plan; adding one would only add governance
weight without a use case.

### Agent framework: Strands vs LangGraph

**Decision: Strands, for every phase.** Not a preference — four
verified facts:

1. **It is already in the repo** (`strands-agents==1.55.1`, `agents/`
   verified live against qwen2.5:7b). LangGraph would be a rewrite of
   working, tested code for no capability gain.
2. **Ollama and llama.cpp are first-class Strands model providers**
   (Tab 7's "finding that makes D4 work") — the same agent code runs
   local now and on AgentCore later by swapping the provider in
   `model_factory.get_model()`. That is the whole Stage 1→3 story.
3. **AgentCore lists Strands explicitly** and is framework-agnostic;
   the bank's own proven pattern is "LangGraph agent in a container
   behind AgentCore Runtime calling the Model Gateway" — Tab 7's note:
   *substitute Strands for LangGraph, same shape.*
4. `strands-agents-tools` and `bedrock-agentcore` are **confirmed on the
   bank Artifactory**; nothing has to be sourced from public PyPI.

Where LangGraph would genuinely be better — explicit graph state,
branching, human-in-the-loop interrupts across long multi-step
workflows — this plan deliberately does not go: the orchestrator stays
plain Python (constraint: no autonomous multi-step agent). The one
place a graph would tempt (A2 investigator gathering evidence in
several steps) is bounded to read-only tools on one client, which
Strands' single agent loop handles. If a later phase ever needs durable
multi-step state, that's the point to re-open the question — not before.

Per-phase mapping: A0/A2/A3 = a `strands.Agent` with a scoped tool list
and `structured_output_model` (already how `DomainAgent` works). A1 =
the same, with no tools (text in, validated `ExogenousEvent` out). A4 =
a coding agent (Claude Code or the bank's equivalent) driven by
`docs/adding_a_new_domain.md` — not a Strands runtime component.

### Scalability to AgentCore on AWS

**Yes, by design — and honestly: the shape is built, the deployment has
never run.** What already exists:

| AgentCore primitive | What the repo has today | Status |
|---|---|---|
| **Runtime** (containerised agent, invoked per request) | `agents/entrypoint.py`'s `@app.entrypoint` on `BedrockAgentCoreApp`; `Dockerfile` | Contract only, **NOT RUN** |
| **Model access** (Model Gateway) | `model_factory.ModelConfig.mode="model_gateway"` branch | `NotImplementedError` by design until Stage 3 sign-off |
| **Gateway** (tools as managed endpoints) | Tools are already pure functions behind a registry; exposing them via AgentCore Gateway/MCP is a wrapper, `mcp` is on Artifactory | Not started |
| **Memory** (session/long-term) | Evidence-hash narrative cache in SQLite; `recommendation_id` + `AgentTrace` (A0) | Local now; AgentCore Memory is a drop-in for A3's per-RM session |
| **Identity** | none needed locally | RM identity for A3 feedback attribution comes from the bank's IdP at Stage 3 |
| **Observability** | `AgentTrace` (A0), `narrative_source` model labels | Maps to AgentCore's OpenTelemetry traces |

What makes the move mechanical rather than a rewrite:

- The **batch** path (all clients, no LLM) is not an agent workload at
  all — it runs as a scheduled job (Glue/Batch/Snowflake task) and
  writes the Pega-shaped record. Only the per-client agent calls
  (A1/A2/A3) go behind AgentCore Runtime, which is exactly what it's
  built for: short, stateless, per-request invocations.
- Every agent is **stateless per request** (client id + as-of in, typed
  record out) — the shape Runtime scales horizontally.
- The provider swap is **one branch in one file**; the localhost
  validator relaxation is the single, governed constraint change D4
  already anticipates.
- Data access moves with `DataSource` config, not code. Event ingestion
  (A1) does not have that same guarantee yet — see the corrections
  above.

What must be true before it actually runs (and is NOT RUN today):
governance Phases 1–2 (AIRA classification, MRO/MO, AIDEA) — the plan's
trace, human-in-the-loop and feedback-capture elements exist precisely
to make that submission possible; the Ollama→Model Gateway swap
authorised; the container built and pushed. None of that is a code
problem; all of it is out of scope for this repo until explicitly
authorised (CLAUDE.md).

## 4. The user journey

**Persona: a commercial RM with 120 clients, 20 minutes on Monday morning.**

1. **Overnight batch (no LLM).** Detectors run per agreement, correlation
   assembles ~20 recommendations, the worklist CSV, digest and
   Pega-shaped record are written. Identical to today.
2. **Overnight, A1.** The extraction agent reads the weekend's notices,
   emits three validated events; one — a public tender award in sector
   C, Spain — is qualified against the book. One client is genuinely
   exposed; a second in the same sector and country is not, and is left
   alone (the negative case the demo already proves).
3. **Worklist.** Row 1: FINANCING_NEED, confirmed by deposits +
   exogenous, strength 4/5, "Offer working-capital financing of ~EUR
   800,000 (25% of the EUR 3.2m tender value, illustrative)", indicative
   revenue EUR 21,200, baseline: deterministic. Row 4 carries an A2
   investigation note: "fixed rate ends in 41 days; client's inflows are
   80% EUR and it holds no hedge — suggests HEDGING_NEED rather than a
   deposit conversation. Unconfirmed."
4. **Ask the copilot (A3).** "Why 800k and not more?" → "Sized as 25% of
   the tender value; the client's own revenue step-change (EVENT_FINANCIAL:
   AGR000060, +76.8%) confirms it. No contract value in our data, so the
   figure is illustrative." Every sentence cites an evidence ref.
5. **Confirm or push back.** RM accepts the HEDGING_NEED refinement on
   row 4 (category updates *now*, by a person). Row 7 marked "Not
   Appropriate — client already refinanced." Both stored with
   `recommendation_id`.
6. **Weeks later (A5, when authorised).** Those responses are the label:
   which hypotheses led to engagement. The score becomes calibrated,
   still explainable, still not a ranker.

**Persona: a developer adding the Risk domain (A4).** Pastes the CRADLE
column extract, runs the onboarding agent, reviews a PR that wires the
two existing Risk detectors, adds the YAML block and tests. Suite green,
`demo_fdm_scenario` now shows RISK_REVIEW suppressing revenue offers for
downgraded clients. No edit to orchestrator, domain agent or hypothesis.

## 5. Sequencing and effort (rough)

| Phase | Depends on | Effort | Value |
|---|---|---|---|
| A0 honest agent + trace | — | days | latency, audit trail, truthful story |
| A1 event extraction | A0 | 1–2 weeks | closes Gap 1; first real agentic capability |
| A3 RM copilot + feedback | A0 | ~1 week | RM adoption; starts the label |
| A2 investigator | A0, registry | ~1 week | better hypotheses on ambiguous signals |
| A4 onboarding agent | registry (done) | ~1 week | maintainability; wires Risk |
| A5 feedback learning | A3 + access | later | calibrated strength, no ranker |

Recommended order: **A0 → A1 → A3 → A4 → A2 → A5.** A1 and A3 together
give the first stakeholder demo that is agentic in substance: an
external event nobody typed in, turned into a sized recommendation an RM
can interrogate and answer.

## 6. Explicitly not in this plan

- No agent that emails, writes to CRM, or publishes to MIMO/Pega/S3.
- No autonomous multi-step planning across domains; the orchestrator
  stays plain Python.
- No paid or cloud model, no Bedrock, until the Stage 3 gate.
- No training on `protected_evaluator_only/` or hidden generator labels.
- No ranker. Pega arbitrates.
