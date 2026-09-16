"""
Hand-written notice text for A1 ingestion (docs/agentic_plan.md) --
NOT generator output, NOT protected_evaluator_only/, disclosed as
synthetic. `NOTICE_MATCHING_FIXTURE` deliberately describes the same
(sector, country, approximate value) as
external_events/output_fdm/tender_events.csv's one row, so running it
through the extraction agent and swapping the result into
agents/demo_fdm_scenario.py proves the SAME positive/negative story
(PRTY00036/PRTY00037) now runs from an extracted sentence instead of a
hand-typed CSV row -- see docs/current_state.md's "A1 wired into the
flow" note for why this specific match matters (an unrelated
sector/country would qualify zero clients in this 60-person book,
proving nothing).
"""

NOTICE_MATCHING_FIXTURE = (
    "TED Notice 2026/S 128-051200: A public infrastructure tender worth "
    "EUR 3,200,000 was awarded on 6 July 2025 to a manufacturing "
    "contractor (NACE section C) operating in Spain (ES)."
)
