"""
Shared boundary-formatting constants for every sink that renders a
datainsights.correlation.hypothesis.Recommendation -- docs/decision_record.md
Tab 4's hard limits, kept in one place so the RM digest/worklist, the MIMO
JSON sink, and the Pega event mock sink all enforce the same numbers
rather than each hardcoding its own copy.

No sink computes its own category, hypothesis, sizing, or ids -- every
sink renders the SAME Recommendation object; see
tests/test_sink_contract.py for the test that proves this across sinks.

Deliberately not a rigid one-method Sink Protocol: the existing sinks
take genuinely different inputs (fdm_worklist.py's build_rm_worklist
needs `source` for client segment/sector/country context that mimo/pega
have no use for) and forcing one signature today would either strip that
context or bloat the others with an unused parameter. What IS shared and
enforced here is the boundary formatting every sink must apply.
"""

from __future__ import annotations

MAX_USER_STORY_ID_CHARS = 30
MAX_CRM_TEXT_CHARS = 200


def snake_to_dot(key: str) -> str:
    """Tab 4: attribute keys are snake_case in Python, converted to
    dot.case on the wire by the MIMO driver (e.g. nba_category ->
    nba.category). Not applied automatically anywhere yet -- no sink in
    this build talks to a real wire format -- but centralised here so the
    one place that eventually does calls this, not reinvents it."""
    return key.replace("_", ".")
