# External events — the exogenous half of the objective

See `docs/objective.md` for why this exists: endogenous events (a client's
own transaction behavior) are one category; this is the other — things
happening in the market/industry/political environment that could be
relevant to a commercial/institutional client, matched by sector and
country rather than watched from their account.

## What's here (since R23: one registry, one feed per schema)

- `config/event_types.yaml` — the declarative registry: how each event
  type is matched, qualified against a client's own data, and correlated
  (hypothesis override, sizing). Adding a type is YAML only.
- `exposure_qualifier.py` — the deterministic qualification (sector /
  country / confirming activity); `exposure_checks.py` — the checks it
  composes; `event_extraction_agent.py` — turns notice text into a
  validated event (A1); `sample_notices.py` — fixtures.
- `output_fdm/tender_events.csv` — the generated feed for the FDM book
  (`python -m data_generator.fdm.generate_fdm_events`); scaled variants
  under `output_fdm_scaled*/`.

The retired legacy simulator/demo (`simulate_external_events.py`,
`demo_scenario.py`, `external_macro_event`) and the legacy
`ExternalEventSource` interface were deleted in R23.

## The event catalog, and what real source replaces each one

Every event type is tagged with the real, free, public API that would
supply it in production — this is the "point to real sources without much
hassle" requirement from the conversation this was built for. Swapping any
one of these in means producing rows in the event feed's columns
(`external_events/exposure_qualifier.py::load_events`) and registering
the type in `config/event_types.yaml`; nothing in the qualifier, the
assembler or the narration layer changes.

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

A real source must produce the feed's columns (event_id, event_date,
event_type, affected_sector, affected_country, direction, severity,
estimated_value_eur, headline, description). **Structured** sources (ECB,
TED, Eurostat, EU sanctions, EM-DAT) map directly — no LLM.
**Unstructured** sources (GDELT, news, social) need the extraction step
first: `event_extraction_agent.py` is that step — a local model reads
text and proposes a structured event, validated or rejected before it
can enter the feed (A1). Start with a structured source, not GDELT.

## Hypothesis and sizing

Every recommendation carries a hypothesis (per signal in
`config/domains_fdm.yaml`; a confirmed event's `correlation.hypothesis`
in `config/event_types.yaml` overrides it) and a sized action whose
basis is stated inline as illustrative (`sizing:` per event type, or the
endogenous sizing rules in `config/rules.yaml`). These are this build's
stated reasoning, not validated causal claims — revisit them when real
accept/reject outcomes exist.

## What this deliberately does NOT claim

A client matching an event's sector/country is evidence the client is
*plausibly* in scope — not that they're actually affected, not that a
specific product helps, not that outreach will convert. Each of those is a
stronger claim needing more evidence than a sector/country join provides.
The narration validator (`agents/domain_agent.py`) actively rejects
overconfident language and requires hedged phrasing for exactly this
reason — see it reject a real Ollama response mid-development for lacking
that hedge, documented in the conversation this was built from.
