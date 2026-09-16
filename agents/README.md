# Domain agents — status and AgentCore staging

This directory implements the "tool-calling domain agent" architecture
for DataInsights: a Strands agent per domain gathers deterministic
detector evidence and narrates it — with the same validate-or-fallback
discipline throughout. It does not decide the final NBA category or size
an offer; that stays with `datainsights/correlation/` (the Hypothesis
Assembler) and `datainsights/fdm_worklist.py`.

**As of M8 (`docs/agentic_plan.md`), the narrating agent gets no tools at
all** — every detector already ran once, deterministically, before the
LLM call; giving the LLM the same tools meant it ran them again itself
for zero effect on the validated output. See `domain_agent.py`'s A0 note.
The three *other* agents in this directory (`investigator_agent.py`,
`rm_copilot_agent.py`) are deliberately even narrower: read-only,
single-client-scoped tools or no data access beyond one worklist row.

## What's here

| File | Purpose | Status |
|---|---|---|
| `model_factory.py` | `get_model(cfg)` — local Ollama now; Model Gateway branch is `NotImplementedError`, contract-only | **Local branch: RUN, verified.** Gateway branch: NOT RUN by design. |
| `domain_registry.py` | Code half of the domain registry (M7) — which tool factory + detector modules each domain owns, registered once per domain in `tools.py` | **RUN, verified** — orchestrator/domain_agent read this generically; adding a domain needs zero edits to either. |
| `tools.py` | Deposits, Lending, Exogenous, and Risk detectors exposed as Strands `@tool` functions, one `register()` call per domain | **RUN, verified against generated FDM dataset**, including the whole-book demo. |
| `domain_agent.py` | `DomainAgent` — gathers tool evidence deterministically, narrates it (no tool access), validates, falls back to a deterministic template | **RUN, verified** (stubbed-model + live-Ollama tests). |
| `investigator_agent.py` | A2 — for a `Recommendation` flagged `ambiguous`, gathers more of that client's own evidence (read-only tools) and *proposes* a category refinement; never changes the category itself | **RUN, verified live** — one open bug: a live run proposed a category contradicting its own stated evidence; category-set validation caught the category, not the reasoning. See `docs/agentic_plan.md`'s A2 section. |
| `rm_copilot_agent.py` | A3 — answers an RM's question about one recommendation using ONLY that worklist row's fields; no tool, no `DataSource` access | **RUN, verified via Streamlit `AppTest`** driving a real click, real local Ollama answer. |
| `entrypoint.py` | `invoke(payload)` plus an `@app.entrypoint`-decorated AgentCore handler | `invoke()`: RUN. AgentCore server (`app.run()`): **NOT RUN, contract-only.** |
| `../Dockerfile` | Container packaging for Stage 3 | **NOT RUN — never built or pushed.** |
| `demo_fdm_scenario.py` | Whole-book batch run (no LLM) — the volume demo | **RUN.** Sources its exogenous event from A1 extraction if available, else the generator fixture. |
| `demo_multiagent_scenario.py` | One client, live LLM narration per domain — the depth demo | **RUN**, ~16.7s (post-A0; was ~24s). |

Supporting, outside this directory: `datainsights/agent_trace.py` (A0 —
audit trail per narration, keyed on `recommendation_id`),
`external_events/event_extraction_agent.py` (A1 — unstructured text →
validated `ExogenousEvent`), `datainsights/rm_feedback.py` (A3 —
Customer Engaged / Not Appropriate / Remind Me Later capture).

## Stage 1 → Stage 3 (docs/decision_record.md Tab 5)

```
STAGE 1 -- Local          Strands + OllamaModel -> localhost:11434.
                           Detectors deterministic, FDM synthetic data,
                           no governance overhead. <- YOU ARE HERE.
   |
   v
STAGE 2 -- Prove one NBA locally
                           End-to-end, shaped as a MIMO insight record
                           (datainsights/sinks/mimo_placeholder.py) and,
                           since M7, a Pega event mock
                           (datainsights/sinks/pega_event_mock.py). Still
                           all local.
   |
   v
STAGE 3 -- AgentCore       Swap OllamaModel -> internal Model Gateway via
                           agents/model_factory.get_model(). Containerise
                           with ../Dockerfile. Enter governance with a
                           working artefact and evidence.
```

`model_factory.get_model()` is the swap point named in the decision
record: "put the provider behind this factory from day one." Moving to
Stage 3 changes `ModelConfig.mode` from `"local"` to `"model_gateway"` and
implements that one branch — no other code in `tools.py`,
`domain_agent.py`, `investigator_agent.py`, or `rm_copilot_agent.py`
should need to change.

## AgentCore governance checklist (docs/decision_record.md Tab 5)

Reproduced here so it travels with the code that will eventually need to
pass it. None of these are done — this repo has not entered AgentCore
governance. Do not claim any row below is complete without an actual
submission/approval to point to.

| Phase | Requirements | Gate |
|---|---|---|
| 1 Pre-dev | AIRA Front Door submission, AIRA Tier 1–4 classification, Model Risk Owner + Model Owner assigned, Inherent Risk Questionnaire, DRA, Security STaRT, PIA | Cannot start dev without AIRA classification + MRO/MO |
| 2 Dev | AIDEA assessment, fairness/bias approach, explainability, monitoring thresholds, data lineage, **human-in-the-loop**, audit trail, **feedback capture** | AIDEA approval before UAT |
| 3 Model gov | MMS registration, Model Tiering, Model Development Documentation, Independent Model Validation (Tier 2–4), KPIs, drift thresholds, retraining cadence | Model Owner go-live approval |
| 4 Pre-prod | MCR, Change Risk Review, STaRT closure, DRA closure, PIA closure, UAT, SRE Production Readiness, rollback plan, CAB | MCR authorised + all closures |
| 5 Post-prod | Monitoring dashboards, feedback capture live, 30-day Model Performance Review, Model Risk Committee reporting, fairness metrics, quarterly AIDEA review | ongoing |

Three things already true by construction, per the decision record's own
note plus this session's build: **human-in-the-loop** (every
`DomainAgentResult`/`InvestigationNote` is a recommendation for RM
review, nothing auto-acts; A2's proposal only changes a category when an
RM confirms it), **audit trail** (`datainsights/agent_trace.py` records
every narration's model, latency, and fallback status against
`recommendation_id` — not a plan, running), and **feedback capture**
(`datainsights/rm_feedback.py` captures the live Customer Engaged / Not
Appropriate / Remind Me Later taxonomy against `recommendation_id`
today, in the actual dashboard — this is real capture now, not just a
documented intention to capture later). Deterministic detection
underneath every agent also keeps this well below the tiering an
autonomous multi-step agent would draw.

## Non-negotiable boundaries this module holds itself to

- **No autonomous multi-step agent.** A `DomainAgent` narrates once per
  `evaluate()` call with no tool access; it does not chain reasoning
  across domains or decide what to investigate next. `investigator_agent.py`
  gathers evidence for ONE flagged signal, once, and proposes — it never
  chains further investigation on its own. Cross-domain correlation is
  plain Python (`datainsights/correlation/`), never an LLM.
- **No agent decides a category, score, offer size, or revenue figure.**
  `investigator_agent.py` may *propose* a refinement as a separate,
  unconfirmed field; the Recommendation's actual `nba_category` is set
  only by `datainsights/correlation/hypothesis.py`'s deterministic
  `assemble()`.
- **No FinCrime/PEP/geopolitical surface.** `domain_agent.py`'s
  `BANNED_TERMS` check (reused by `investigator_agent.py` and
  `rm_copilot_agent.py`) rejects any narrative mentioning sanctions, PEP
  status, or money laundering — enforced in code, not just by prompt
  instruction.
- **Local-first, no paid/cloud calls.** `get_model()` routes through
  `datainsights.config.LLMConfig`'s existing validator; the
  Model-Gateway branch is a documented `NotImplementedError`, not a stub
  that silently degrades to something that works.
- **Deterministic fallback, never an unvalidated claim.** Every agent in
  this directory either returns LLM output that passed its own
  validation, or a template/fact-list built directly from evidence with
  no LLM involved at all.
- **Tightest scope for the agent a person talks to.** `rm_copilot_agent.py`
  has no tool and no `DataSource` access at all — its only "context" is
  one worklist row, so there is structurally nothing else it could leak
  even if it tried. `investigator_agent.py`'s tools are read-only and
  closed over one `prty_id`, one level looser since it needs to gather
  live evidence, not just restate a row.
