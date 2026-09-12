# DataInsights — condensed project context (for handoff to another Claude session)

**Purpose of this file**: a single, self-contained document capturing the
full context of this project — objective, architecture, decisions, current
state, and working conventions — so it can be pasted into a Claude session
in a different, more restricted environment (e.g. a corporate machine that
blocks/filters script uploads) and that session can understand the project
without seeing the actual codebase. Paste this whole file as the first
message, or as a system/context message, before asking for help.

Last synced against the real repo: 2026-09-12.

---

## 1. What this is, in one paragraph

A proof-of-concept for a commercial/institutional banking **Next-Best-Action
(NBA) / Event-Based-Marketing (EBM)** system. Relationship managers today
reach out to clients on a calendar (quarterly check-ins), not on a signal.
This system watches two kinds of signal a calendar can't: a client's **own**
transaction behavior (endogenous events — e.g. an unusually large incoming
payment), and **external** market/political/industry events near them
(exogenous events — e.g. a sanctions change, a public tender award, an
energy price shock). It detects these, ranks them transparently, sizes a
concrete recommended action per client, and hands it to an RM as a
human-reviewed worklist — never automating outreach itself.

Scope: commercial/institutional clients only (SME / Mid-Corporate /
Large-Corporate / Institutional segments — never retail, never private
banking), European market (EU/EEA, EUR-default, multi-currency-aware).

## 2. Non-negotiable constraints (apply everywhere in this project)

- **Local inference only, no paid/cloud LLM calls, ever.** Enforced in
  code (hard-coded validators reject Ollama `-cloud` tags, non-localhost
  endpoints, and any "paid calls allowed" flag) — not just documented policy.
- **Human review before any action.** Nothing in this system emails a
  client, writes to a CRM, or takes an action. It produces a reviewable
  recommendation; a person decides.
- **No autonomous multi-step agent.** An LLM may extract structure from
  unstructured evidence, and may write a narrative from already-verified
  facts. It never chains multi-hop causal reasoning on its own ("war →
  prices → client's margin → act") and never decides what to search for next.
- **Ground truth stays isolated from the detector.** Any synthetic label
  used to verify a detector lives in a physically separate, code-blocked
  directory (`protected_evaluator_only/`) that the detection code cannot
  import a path to. Enforced at three independent layers: directory
  boundary, schema/contract boundary, and code-level path refusal.
- **Deterministic first, ML only when justified, LLM only where it earns
  its keep.** Statistics/rules before ML; an explicit, auditable ranking
  formula before a trained ranker; an LLM only at extraction
  (unstructured → structured) and narrative (structured → readable)
  layers — always validated against the evidence, always able to fall
  back to a deterministic template or abstain, never the decision-maker.
- **No production deployment, no real external communication.** No AWS
  resource provisioning, no converting a Snowflake trial to paid, no
  emails/CRM writes — this is a proof-of-concept, explicitly.

## 3. Architecture — two parallel pipelines, one unified output

```
ENDOGENOUS PIPELINE (a client's own transactions)
  OfflineLocalSource (DuckDB/CSV)  [Snowflake adapter wired, NOT RUN]
    -> large_incoming_payment detector (rolling MAD vs. 90-day trailing baseline)
    -> ranking.rank() (magnitude + recency, explicit formula, clipped [0,1])
    -> local Ollama narrator (validated) -> deterministic template (fallback)
    -> SQLite state -> markdown digest

EXOGENOUS PIPELINE (external events, parallel, same shape)
  SimulatedExternalEventSource (CSV; real APIs: ECB, TED, Eurostat,
    EU sanctions/OpenSanctions, EUR-Lex, GDELT, EM-DAT)
    -> external_macro_event detector (sector/country match + cooldown)
    -> ranking.rank_macro() (severity + recency, same shared formula shape)
    -> macro narrator (hedged-language validator) -> macro template (fallback)
    -> SQLite state -> markdown digest

BOTH -> datainsights/worklist.py -> ONE unified, row-per-client CSV worklist
  (category tag + hypothesis + sized recommended action + evidence)
```

Key design point: swapping the source backend (local CSV → Snowflake) only
changes one adapter behind a shared `DataSource` interface — detection,
ranking, narrative, and worklist code never change. Verified true today
because the Snowflake adapter is written but never executed (no credentials
in this environment) — the interface contract is what guarantees it, not
a live test.

## 4. Where each kind of "intelligence" sits (and why)

| Stage | Technique | Why |
|---|---|---|
| Detection — transaction | **Statistical** | Rolling median absolute deviation vs. trailing baseline. Explainable, no training data needed. |
| Detection — external event | **Rule-based** | Sector/country match — a join, not a model. Nothing to overfit. |
| Ranking (both pipelines) | **Statistical** | Explicit magnitude+recency formula, every component visible in the output. No opaque score. |
| Narrative (evidence → text) | **LLM (local Ollama)** | Validated against the evidence packet before acceptance; deterministic template fallback on any validation failure. Never invents a fact not in the evidence. |
| Quality check | **LLM (local Ollama)** | Sampled judge, deliberately a *different* model than the narrator, to reduce (not eliminate) correlated errors. |
| Category tagging | **Rule-based** | Static/direction-conditional lookup table (e.g. a rate cut → financing; a rate rise → treasury). Not a model. |
| Offer sizing (the amount) | **Rule-based, disclosed heuristic** | See §6 below. |
| Machine learning | **Not built** | Deliberately deferred — no real RM accept/reject outcome data exists yet to train against. Every "not yet ML" decision is intentional, not a gap someone forgot. |

## 5. The six recommendation categories, and which ones are actual bank revenue

Every detection (either pipeline) gets tagged with exactly one of six
categories. Four are real revenue mechanisms; two are defensive/relationship
only — this distinction matters and is stated explicitly in the code's own
docstrings, not just in conversation:

| Category | Bank revenue mechanism? | Mechanism |
|---|---|---|
| `FINANCING_NEED` | **Yes** | Interest income + origination fees on a new loan/credit line/guarantee |
| `TREASURY_OPPORTUNITY` | **Yes** | Fee/spread income from a deposit or short-term investment product |
| `HEDGING_NEED` | **Yes** | Fee income from an FX/commodity hedge |
| `CAPEX_FINANCING` | **Yes** | Interest income on equipment/transition financing |
| `RISK_REVIEW` | No — defensive | Compliance/counterparty exposure review (sanctions, geopolitical) — protects existing revenue, isn't a sales trigger |
| `ADVISORY_ONLY` | No — relationship | A conversation worth having, no clear product yet |

## 6. The hypothesis → sized action pattern (the most recently built, and most business-facing, part of this system)

The business-facing ask that drove the most recent build phase: **for
every matched client, state the general hypothesis for why this event
implies a need, then give a specific, sized, per-client action** — not a
generic "RM to review."

Two layers, deliberately kept separate:

1. **`hypothesis`** — one sentence of *general* reasoning, e.g.: *"Winning
   a public tender creates a cash-flow gap between delivery and payment —
   the winner needs working capital sized to the contract, not their
   balance sheet, and needs it before delivery starts."* Same sentence for
   every client matched to that event type + direction. Lives in a
   lookup table keyed by `(event_type, direction)`, mirroring the category
   lookup table.
2. **Sized `recommended_action`** — a concrete number *per client*, e.g.:
   *"Offer working-capital financing of ~€1,973,422 (25% of the
   €7,893,686 tender value, illustrative) to bridge delivery before
   payment."*

How the number is grounded, honestly:
- Two simulated event types (`public_tender_award`, `natural_disaster`)
  were given a simulated monetary magnitude (`estimated_value_eur`),
  because their **real** sources (TED, EM-DAT) actually publish exactly
  that — a contract award value, an estimated damage figure. This was
  added specifically so a concrete number could be stated *honestly*
  (as simulated data) rather than invented in a conversation.
- The offer size is then a disclosed **percentage of that event value**
  (e.g. 25% of tender value as a working-capital advance), capped at a
  multiple of the client's own annual revenue so a large regional event
  doesn't produce an implausible ask against a small client.
- For event types with no natural per-event value (a rate move, an energy
  price index shift, a new regulation), the offer is sized as a disclosed
  **percentage of the client's own revenue** instead (e.g. 15% of revenue
  for a rate-cut-driven financing review).
- For the transaction pipeline, no heuristic is needed at all — the
  amount is the *actual* flagged transaction value.
- **Every percentage is stated inline as "illustrative"** in the output
  string itself — e.g. "(25% of the €X tender value, illustrative)" — so
  nobody downstream mistakes a planning heuristic for a validated
  conversion benchmark. This is a hard rule this project holds itself to:
  never assert more certainty than the evidence supports.
- Two categories (`RISK_REVIEW` — sanctions, geopolitical disruption)
  deliberately get **no** sized number. Sizing a financing offer off a
  compliance/risk signal would be the wrong move, so the code doesn't do it.

This hypothesis + sizing logic is written into the actual pipeline output
(the worklist CSV and every RM digest), not produced ad hoc — every one of
~3,000 rows in a real run carries both fields automatically.

## 7. Current state — what's actually verified vs. not

**Built, running, verified end-to-end, zero paid/cloud calls:**
- Synthetic dataset generator (300 commercial/institutional clients,
  ~300K transactions, realistic IBAN/LEI/NACE/ISO-20022 schema) + an
  independently-seeded holdout dataset
- Typed, validated runtime config (Pydantic; hard-blocks unsafe settings
  in code, not just YAML)
- Endogenous detector (`large_incoming_payment`) — point-in-time correct,
  idempotent, cooldown-suppressing
- Exogenous detector (`external_macro_event`) — sector/country match,
  cooldown-suppressing
- Shared ranking formula (magnitude + recency, two thin wrappers for the
  two detector families)
- Two narrator families (transaction-shaped, macro-event-shaped), each
  with a validate-or-fallback-to-template pattern
- Offline-sampled judge (different model than narrator)
- SQLite state store, idempotent, narrative caching by evidence hash
- Unified worklist generation (category + hypothesis + sized action +
  evidence, one CSV, both pipelines)
- A local Streamlit dashboard (`dashboard/app.py`) — data source previews,
  pipeline run buttons, filterable worklist, digests, technique reference,
  status — everything read-only against real files except the run buttons,
  which shell out to the exact same modules the CLI uses
- 16/16 automated tests passing (both detector families)
- A dev-diagnostic evaluation harness against embedded ground truth
  (**explicitly disclosed as contaminated** — the same session that wrote
  the label-injection logic also wrote and evaluated the detector; numbers
  from it are a development diagnostic, not a clean benchmark)

**Explicitly NOT built / NOT run:**
- Snowflake: adapter code exists (same `DataSource` interface), has
  **never executed** — no credentials in this environment
- Replay/monitor mode with a virtual clock and incremental checkpoints
- The other 7 endogenous trigger types labeled in the data but undetected
  (only 1 of 8 has a real detector)
- ML ranking challenger — deferred on principle (§4), not a gap
- A review/tracking dashboard with reviewed/actioned/dismissed state
  (the Streamlit dashboard is a *demo viewer*, explicitly not this)
- Real (non-simulated) external event sources — the interface is built
  for this, nothing plugs into a live API yet
- AWS/Bedrock/AgentCore integration — contract-and-mock only, by policy

## 8. Known limitations to state honestly if this work continues

- **Contamination**: any precision/recall/judge number from this
  session's own dataset is a development diagnostic, not a validated
  detection-quality claim, because the same session wrote the injection
  logic that creates the labels being measured against.
- **Illustrative heuristics, not benchmarks**: every sizing percentage
  (25% of tender value, 15% of revenue, etc.) is a planning assumption
  this build chose, disclosed inline, not derived from real conversion data.
- **Category tags are best-guesses**: the six-category mapping (e.g. "a
  rate cut → financing conversation") is an honest inference from the
  event's shape, not a validated product recommendation.
- **Correlated judge/narrator risk**: both are local Ollama models with
  unknown training-data overlap — a different model reduces but doesn't
  eliminate correlated errors.
- **GDPR/EU AI Act**: checked (not assumed) — this design sits outside
  both because sector/country matching operates on legal entities, never
  correlates with a politically-exposed-person flag, and produces no
  individual-level creditworthiness score. That boundary (never combine
  a PEP flag with a political/geopolitical event type) is a real
  constraint to keep, not a solved problem to revisit casually.

## 9. Repo structure (key files only)

```
data_generator/generate_data.py         synthetic dataset generator
data_generator/output/                  the dataset itself; protected_evaluator_only/ is ground truth, code-blocked
detection_engine/large_incoming_payment.py   endogenous detector
detection_engine/external_macro_event.py     exogenous detector
datainsights/ranking.py                 shared ranking formula (rank() + rank_macro())
datainsights/narrative/                 both narrator families + evidence packets
datainsights/worklist.py                category tags, hypothesis lookup, offer sizing, unified CSV builder
datainsights/state.py                   SQLite state/idempotency/narrative cache
datainsights/runner.py, cli.py          endogenous pipeline orchestration
external_events/                        exogenous event simulator + demo runner + README (real-source mapping)
dashboard/app.py                        Streamlit demo dashboard
docs/objective.md                       the reference doc — check any new decision against this first
docs/architecture.md                    full system design + diagram
docs/current_state.md                   verified vs. NOT RUN vs. not built (may lag; this file is the fresher summary)
docs/gap_analysis.md                     current state vs. objective, table form
docs/compliance_and_industry_context.md GDPR/EU AI Act boundary checks, dated
docs/artifacts/                         exported presentation HTML (pipeline diagrams, sponsor pitch, demo run sheet)
run_demo.sh                             one command: tests -> both pipelines -> evaluation -> worklist
```

## 10. Working conventions this project holds itself to (useful if another session continues this work)

- **Verify, don't assert.** Every claim of "N tests pass" or "the pipeline
  ran" in this project's history was backed by an actual run in that
  session, not stated from memory of an earlier run.
- **Separate verified facts from assumptions explicitly**, every time —
  in code comments, in docstrings, in any report back.
- **Fix root causes, not symptoms.** E.g.: rather than answer a
  hypothetical dollar figure in conversation, the simulator was changed to
  produce an honest one.
- **Disclose limitations inline, in the artifact itself** — not just in a
  conversation that won't travel with the file. The "illustrative" tags on
  every sized offer are a direct example of this.
- **One phase at a time.** Don't rebuild what already works; extend it.
- **No fabricated precision.** If a number isn't grounded in either real
  data or a disclosed heuristic, don't state it.

---

*End of condensed context. If continuing this work in a new environment,
re-establish the same non-negotiable constraints (§2) before writing any
new code — they are the load-bearing part of this project, not
formatting preference.*
