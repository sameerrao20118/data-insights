# External events — the exogenous half of the objective

See `docs/objective.md` for why this exists: endogenous events (a client's
own transaction behavior) are one category; this is the other — things
happening in the market/industry/political environment that could be
relevant to a commercial/institutional client, matched by sector and
country rather than watched from their account.

## What's here

- `simulate_external_events.py` — generates `output/external_events.csv`:
  a mix of hand-authored "scenario" events (dated, narrative-rich, used by
  the demo) and randomly sampled "routine" events across 2023-2025, all
  clearly synthetic.
- `demo_scenario.py` — runs the full pipeline (match → rank → narrate →
  digest) against the real `data_generator/output/clients.csv` and writes
  a digest to `var/insights/`. Run: `python -m external_events.demo_scenario`.
- `output/external_events.csv` — the generated data (gitignored, regenerate
  with `python external_events/simulate_external_events.py`).

## The event catalog, and what real source replaces each one

Every event type is tagged with the real, free, public API that would
supply it in production — this is the "point to real sources without much
hassle" requirement from the conversation this was built for. Swapping any
one of these in means writing a class that implements
`datainsights/sources/external_event_source.py`'s `ExternalEventSource`
interface; nothing in `detection_engine/external_macro_event.py`,
`datainsights/ranking.py`, or the narrative layer needs to change.

| event_type | Simulated today | Real source | Structured or needs extraction? |
|---|---|---|---|
| `rate_policy_change` | ECB decision (simulated) | ECB SDMX API | Structured — direct field mapping |
| `public_tender_award` | TED award notice (simulated) | TED (Tenders Electronic Daily) API | Structured |
| `commodity_energy_shock` | Eurostat index update (simulated) | Eurostat / ECB energy statistics | Structured |
| `sanctions_regulatory_change` | EU sanctions update (simulated) | EU consolidated sanctions list / OpenSanctions | Structured |
| `eu_regulatory_change` | EUR-Lex notice (simulated) | EUR-Lex | Mixed — dates/sectors structured, implications need reading |
| `geopolitical_disruption` | GDELT digest (simulated) | GDELT Project | **Needs extraction** — noisy, unstructured, highest risk of a confident-but-wrong read |
| `natural_disaster` | EM-DAT record (simulated) | EM-DAT International Disaster Database | Structured |

## The extension pattern for a real source

`ExternalEventSource.read_events()` must return rows with the columns in
`REQUIRED_COLUMNS` (event_id, event_date, event_type, source_name,
real_source_type, source_context, affected_country, affected_sector,
direction, severity, estimated_value_eur, headline, description) —
already-structured, joinable data. `estimated_value_eur` is populated only
for `public_tender_award` and `natural_disaster` — the two types whose
real source (TED, EM-DAT) actually publishes a monetary figure per record
(contract award value; estimated damage). It's NaN for every other type on
purpose, not a gap to fill in: a rate move or a sanctions update has no
natural single "value" the way a tender or a disaster does. That
requirement forces a design decision the conversation settled explicitly:

- **Structured sources** (ECB, TED, Eurostat, EU sanctions, EM-DAT) map
  directly — write a class that calls the API and reshapes the response
  into those columns. No LLM needed.
- **Unstructured sources** (GDELT, a news API, a social feed like
  Twitter/X) need a genuinely separate **extraction** step first — an LLM
  or NLP pipeline that reads raw text and produces those same structured
  columns, with its own validation gate (same pattern as
  `datainsights/narrative/macro_narrator.py`'s validate-or-fallback, just
  applied to extraction instead of narration). That extraction step is
  new code, not part of this interface — `read_events()` must still return
  already-structured rows either way. Building it is future work, flagged
  deliberately rather than attempted now (see `docs/gap_analysis.md`);
  start with a structured source if you pick this up, not GDELT.

## Stating the hypothesis, not just the action

Every worklist row and every digest entry now carries a `hypothesis` line
(`datainsights/worklist.py::macro_hypothesis()` /
`TRANSACTION_HYPOTHESIS`) -- one sentence stating *why* this event/
direction combination implies this kind of need, one level up from the
specific per-client number below it. E.g. "a policy rate cut lowers
borrowing costs economy-wide -- clients with existing or planned debt have
a live reason to refinance..." An RM can restate the hypothesis before
getting to the client-specific figure, instead of the worklist just
asserting a number with no reasoning attached. Same honesty rule as
sizing: these are this build's stated reasoning, not a validated causal
claim.

## Sizing a concrete offer from an event

`datainsights/worklist.py::size_macro_action()` turns `estimated_value_eur`
(and the client's own `annual_revenue_eur_est` from `clients.csv`) into a
concrete, sized `recommended_action` for every matched client — not just
the handful that get a full LLM narrative. E.g. a tender worth €X becomes
a working-capital offer of ~25% of €X, capped at a multiple of the
client's own revenue so a large regional event doesn't produce an
implausible ask against a small client's scale. Every percentage is
**stated inline as "illustrative"** — these are planning heuristics this
build chose, not benchmarks derived from real conversion data, and the
function's own docstring says so. Revisit them the moment real
accept/reject outcomes exist to calibrate against.

## What this deliberately does NOT claim

A client matching an event's sector/country is evidence the client is
*plausibly* in scope — not that they're actually affected, not that a
specific product helps, not that outreach will convert. Each of those is a
stronger claim needing more evidence than a sector/country join provides.
The narrative validator (`macro_narrator.py`) actively rejects
overconfident language and requires hedged phrasing for exactly this
reason — see it reject a real Ollama response mid-development for lacking
that hedge, documented in the conversation this was built from.
