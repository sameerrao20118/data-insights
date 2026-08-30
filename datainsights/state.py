"""
SQLite state store: durable detections (idempotent by detection_id), run
history/audit, and narrative cache (keyed by evidence hash + rule/model/
schema version, so unchanged evidence never regenerates a narrative --
project instructions section 8).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,
    profile TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS detections (
    detection_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    client_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    detection_as_of TEXT NOT NULL,
    flagged_amount REAL NOT NULL,
    status TEXT NOT NULL,
    rank INTEGER,
    score REAL,
    first_seen_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narratives (
    evidence_hash TEXT PRIMARY KEY,
    detection_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    narrative_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def start_run(con: sqlite3.Connection, run_id: str, mode: str, profile: str) -> None:
    con.execute(
        "INSERT INTO runs (run_id, started_at, mode, profile, status) VALUES (?, ?, ?, ?, 'running')",
        (run_id, datetime.now(timezone.utc).isoformat(), mode, profile),
    )


def finish_run(con: sqlite3.Connection, run_id: str, status: str, notes: str = "") -> None:
    con.execute(
        "UPDATE runs SET finished_at = ?, status = ?, notes = ? WHERE run_id = ?",
        (datetime.now(timezone.utc).isoformat(), status, notes, run_id),
    )


def upsert_detections(con: sqlite3.Connection, run_id: str, ranked_df) -> int:
    """Idempotent: a detection_id already known keeps its first_seen_run_id
    and is updated in place (rank/score may change as new detections join
    the ranking), never duplicated."""
    n_new = 0
    for _, row in ranked_df.iterrows():
        existing = con.execute(
            "SELECT first_seen_run_id FROM detections WHERE detection_id = ?",
            (row["detection_id"],),
        ).fetchone()
        first_seen = existing[0] if existing else run_id
        if not existing:
            n_new += 1
        con.execute(
            """INSERT INTO detections
               (detection_id, run_id, rule_version, client_id, account_id, currency,
                transaction_id, event_date, detection_as_of, flagged_amount, status,
                rank, score, first_seen_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(detection_id) DO UPDATE SET
                 run_id=excluded.run_id, status=excluded.status,
                 rank=excluded.rank, score=excluded.score""",
            (row["detection_id"], run_id, row["rule_version"], row["client_id"],
             row["account_id"], row["currency"], row["transaction_id"], row["event_date"],
             row["detection_as_of"], float(row["flagged_amount"]), row["status"],
             int(row["rank"]), float(row["score"]), first_seen),
        )
    return n_new


def get_cached_narrative(con: sqlite3.Connection, evidence_hash: str, prompt_version: str,
                          model: str, schema_version: str) -> dict | None:
    row = con.execute(
        """SELECT narrative_json FROM narratives
           WHERE evidence_hash = ? AND prompt_version = ? AND model = ? AND schema_version = ?""",
        (evidence_hash, prompt_version, model, schema_version),
    ).fetchone()
    return json.loads(row[0]) if row else None


def cache_narrative(con: sqlite3.Connection, evidence_hash: str, detection_id: str,
                     rule_version: str, prompt_version: str, model: str,
                     schema_version: str, narrative: dict) -> None:
    con.execute(
        """INSERT OR REPLACE INTO narratives
           (evidence_hash, detection_id, rule_version, prompt_version, model,
            schema_version, narrative_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (evidence_hash, detection_id, rule_version, prompt_version, model, schema_version,
         json.dumps(narrative), datetime.now(timezone.utc).isoformat()),
    )
