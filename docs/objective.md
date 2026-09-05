# Objective

**This is the reference document.** When a design choice, a new detector, or
a scope question comes up anywhere else in this project, it gets checked
against this file, not re-litigated from scratch. If something built
elsewhere drifts from this, that's a signal to update this file
deliberately, not to let the drift stand silently.

## The one-sentence objective

Detect meaningful events — both in a client's own transaction behavior and
in the external market/industry/political environment around them — rank
the resulting opportunities transparently, and hand a Relationship Manager
an evidence-backed, human-reviewed summary, for commercial and
institutional banking clients in the European market.

## Scope

- **Client base**: commercial and institutional clients only (SME,
  Mid-Corporate, Large-Corporate, Institutional segments) — never retail,
  never private/wealth banking (see the earlier scope decision in
  `data_dictionary.md`'s history).
- **Geography**: European market — EU/EEA countries, EUR-denominated
  business as the default case, with the multi-currency handling already
  built in for clients trading outside it.
- **Two event categories**, deliberately named this way and referred to
  by these names everywhere else in the project:
  - **Endogenous events** — patterns in a client's own transaction/account
    history. Eight designed, one built (`large_incoming_payment`). Detected
    purely from data this bank already has.
  - **Exogenous events** — things happening in the world, not in the
    account: rate changes, public tender awards, commodity/energy price
    shocks, sanctions changes, regulatory changes, geopolitical
    disruption, natural disasters. Detected by matching an external signal
    to a client's sector/country, not by watching their transactions.

## Non-negotiable constraints (apply to both event categories)

- **Local inference only, no paid calls** — enforced in code
  (`datainsights/config.py`), not just documented.
- **Human review before any action** — nothing in this system emails a
  client, updates a CRM, or takes an action. It produces a reviewable
  recommendation; a person decides.
- **No autonomous multi-step agent** — an LLM may extract structure from
  unstructured evidence (a news event's affected sector/country/severity)
  and may write a narrative from already-verified evidence. It never
  chains multi-hop causal reasoning ("war → prices → client's margin →
  act") on its own, and it never decides what to search for next. See the
  reasoning in the conversation this was decided in — that boundary is
  deliberate, not a temporary limitation.
- **Ground truth stays isolated from the detector** — whether the event
  is endogenous or exogenous, whatever synthetic/injected label makes it
  verifiable stays in `protected_evaluator_only/`, never in the detector's
  input path.
- **Deterministic first, ML only when justified, LLM only where it earns
  its keep** — a rules/statistics detector before an ML model; an
  explicit ranking formula before a trained ranker; an LLM only at
  extraction (unstructured → structured) and narrative (structured →
  readable) layers, always validated, always able to fall back or
  abstain.

## What "done" looks like for this POC

Not: proven revenue, a production deployment, or a fully autonomous
system — none of those are honest claims a synthetic dataset can support
(see the revenue-case discussion this file is downstream of). Done means:

1. At least one working detector in each event category, each with a
   written spec, tests, and a demonstrable simulation an outsider could
   run and understand.
2. A ranking and narrative layer that treats both categories uniformly
   from the RM's point of view — one digest, not two incompatible
   systems bolted together.
3. A simulation architecture for exogenous events built the same way the
   transaction generator was: structured, documented, and designed so a
   real source (a market data API, a news API, a social feed) can
   implement the same interface later without touching detector code.
4. Every gap between this and a production system disclosed, not
   glossed over — see `gap_analysis.md`.

## Explicitly out of scope, and why

- **Revenue/ROI proof** — requires real outcome data and a causal design
  (a holdout of un-actioned clients), neither of which exists or can be
  faked honestly with synthetic data.
- **Private/wealth banking** — different product domain, different data
  model, decided out of scope earlier.
- **Real email/CRM delivery** — the project's own hard boundary; a draft
  digest is the terminal output, always.
- **Full autonomous agentic orchestration** — see the constraint above;
  revisit only if a specific, narrow, human-in-the-loop case justifies it,
  never "to demonstrate agents."
