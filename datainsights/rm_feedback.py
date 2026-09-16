"""
RM feedback capture (docs/agentic_plan.md A3 / D6) -- the live RM
response taxonomy (docs/decision_record.md Tab 1/2), reused verbatim:
Customer Engaged / Not Appropriate / Remind Me Later, each with a
sub-reason, stored against `recommendation_id`. In production this
arrives from Pega -> CRM instead of this dashboard; the schema is the
same either way, so switching the source later is a no-op for anything
that reads this table.

This is the actual start of the D6 feedback loop -- SLOT E4
(datainsights/ml/slots.py's UnavailablePropensityModel) stays
unimplemented (blocked on real access, per D6), but the label it would
eventually train on is now genuinely being captured, not just planned
for. Nothing here reads or writes protected_evaluator_only/ or any
generator label -- this is exclusively RM-entered, this-session-only
feedback on synthetic recommendations.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RESPONSE_ACTIONS = ("Customer Engaged", "Not Appropriate", "Remind Me Later")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rm_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id TEXT NOT NULL,
    prty_id TEXT NOT NULL,
    response TEXT NOT NULL,
    sub_reason TEXT,
    recorded_by TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rm_feedback_recommendation
    ON rm_feedback(recommendation_id);
"""


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


def record_feedback(con: sqlite3.Connection, *, recommendation_id: str, prty_id: str,
                    response: str, sub_reason: str = "", recorded_by: str = "") -> None:
    if response not in RESPONSE_ACTIONS:
        raise ValueError(f"response must be one of {RESPONSE_ACTIONS}, got {response!r}")
    con.execute(
        """INSERT INTO rm_feedback (recommendation_id, prty_id, response, sub_reason, recorded_by, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (recommendation_id, prty_id, response, sub_reason, recorded_by,
         datetime.now(timezone.utc).isoformat()),
    )


def feedback_for_recommendation(con: sqlite3.Connection, recommendation_id: str) -> list[dict]:
    cur = con.execute(
        "SELECT response, sub_reason, recorded_by, recorded_at FROM rm_feedback "
        "WHERE recommendation_id = ? ORDER BY recorded_at DESC", (recommendation_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
