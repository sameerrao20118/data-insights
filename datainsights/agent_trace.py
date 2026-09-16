"""
Agent execution trace (M8/A0) -- audit trail for every LLM narration call
in the FDM-aligned build, independent of the legacy pipeline's
datainsights/state.py (same "two parallel builds" split as the rest of
the FDM path; see docs/current_state.md).

Exists for one reason stated in docs/agentic_plan.md's A0: AgentCore
governance (docs/decision_record.md Tab 5, Phase 2) requires "data
lineage... audit trail" before AIDEA approval. Recording which model ran,
how long it took, and whether it fell back -- keyed to the
recommendation_id it contributed to -- means that requirement is met by
construction rather than retrofitted once governance actually asks.

Never stores prompt or narrative text bodies (nothing sensitive; this is
synthetic data) beyond what DomainAgentResult already carries -- narrower
than needed is safer than wider than needed for an audit table.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prty_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    recommendation_id TEXT,
    narrative_source TEXT NOT NULL,
    latency_seconds REAL NOT NULL,
    fell_back INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_traces_recommendation
    ON agent_traces(recommendation_id);
"""


@dataclass(frozen=True)
class AgentTrace:
    prty_id: str
    domain: str
    narrative_source: str
    latency_seconds: float
    fell_back: bool
    recommendation_id: str | None = None


@contextmanager
def connect(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def record(con: sqlite3.Connection, trace: AgentTrace) -> None:
    con.execute(
        """INSERT INTO agent_traces
           (prty_id, domain, recommendation_id, narrative_source, latency_seconds, fell_back, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (trace.prty_id, trace.domain, trace.recommendation_id, trace.narrative_source,
         trace.latency_seconds, int(trace.fell_back), datetime.now(timezone.utc).isoformat()),
    )


def traces_for_recommendation(con: sqlite3.Connection, recommendation_id: str) -> list[dict]:
    cur = con.execute(
        "SELECT prty_id, domain, narrative_source, latency_seconds, fell_back, created_at "
        "FROM agent_traces WHERE recommendation_id = ? ORDER BY created_at", (recommendation_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
