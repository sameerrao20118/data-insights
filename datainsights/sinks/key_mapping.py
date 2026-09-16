"""
D7 boundary module -- docs/decision_record.md Tab 1 ("PRTY_ID internally,
map at boundary. One isolated mapping module.") and Tab 4's "Key mapping
-- isolate in one module": "So: key on PRTY_ID internally, map at the
boundary, and put that mapping in ONE module so it becomes a no-op when
the FDM conversion lands. Do not spread it through detector code."

This IS that one module. Every sink that needs an external key calls
through here -- never PRTY_ID -> external-key logic inline in a sink.

Interface + mock only, per CLAUDE.md ("no production deployment or
external communication") and this repo's own NOT RUN discipline: no real
prophet_party_id/CIN or Enterprise Customer ID mapping table exists yet
(Pega's own HLDD records FDM key alignment as "N -- to be revised in
future"). Both functions below are identity passthroughs today, so
calling code is already written against the eventual real mapping and
becomes a no-op change when one lands -- the whole point of isolating it
here rather than waiting.
"""

from __future__ import annotations


def to_prophet_party_id(prty_id: str) -> str:
    """MIMO boundary key (docs/decision_record.md Tab 4's
    prophet_party_id/CIN). NOT RUN as a real mapping -- identity
    passthrough until a real mapping table is available."""
    return prty_id


def to_enterprise_customer_id(prty_id: str) -> str:
    """Pega boundary key -- Interaction History key today
    (docs/decision_record.md Tab 4's Key mapping table). NOT RUN as a
    real mapping -- identity passthrough until Pega's FDM key alignment
    (its own HLDD: "N -- to be revised in future") actually lands."""
    return prty_id
