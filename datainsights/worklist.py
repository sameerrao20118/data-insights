"""
The RM worklist: one row per (client, active recommendation), across BOTH
event categories (endogenous transaction detections and exogenous macro
events) -- built from persisted state, not from a fresh detection run, so
it can be regenerated any time without re-calling the LLM.

This exists because a narrative digest (datainsights/digest.py) is good
for reading one recommendation closely, but useless for triaging a whole
book of clients -- an RM needs to scan, sort, and filter. This is that
view: client profile + category + recommended action + compact evidence,
one row each, CSV so it opens directly in a spreadsheet.

CATEGORY is the answer to "which kind of suggestion is this" -- it always
means the same six things regardless of which detector produced the row:

  FINANCING_NEED       -- likely needs a loan, guarantee, or credit line
  HEDGING_NEED         -- likely needs an FX/commodity hedge
  TREASURY_OPPORTUNITY -- likely has surplus cash for deposit/investment
  CAPEX_FINANCING      -- likely needs equipment/transition financing
  ADVISORY_ONLY        -- relationship conversation, no clear product yet
  RISK_REVIEW          -- credit/risk awareness, not a sales opportunity

These are honest best-guess tags from the event's shape, not a validated
product recommendation -- see each mapping's comment for the reasoning,
and revisit them if real conversion data ever shows a mapping is wrong.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

WORKLIST_COLUMNS = [
    "rank", "score", "category", "client_id", "legal_name", "segment", "sector",
    "country", "relationship_manager_id", "event_type", "recommended_action",
    "evidence_summary", "event_date", "detection_id", "narrative_source",
]

# large_incoming_payment always suggests the same commercial angle: a
# client's own cash just moved in a way that suggests a treasury/lending
# conversation, regardless of which specific transaction triggered it.
TRANSACTION_RULE_CATEGORY = {
    "large_incoming_payment.v1": "TREASURY_OPPORTUNITY",
}

# macro event_type -> category, direction-dependent where the same event
# type cuts both ways (a rate cut is a different conversation than a rate
# rise). Anything not resolvable this simply falls back to ADVISORY_ONLY
# rather than guessing.
def categorize_macro_event(event_type: str, direction: str) -> str:
    if event_type == "rate_policy_change":
        # a cut makes borrowing cheaper (financing conversation); a rise
        # makes deposits more attractive (treasury conversation)
        return "FINANCING_NEED" if direction == "positive" else "TREASURY_OPPORTUNITY"
    if event_type == "public_tender_award":
        return "FINANCING_NEED"  # winning a tender creates a working-capital need
    if event_type == "commodity_energy_shock":
        return "HEDGING_NEED" if direction == "negative" else "ADVISORY_ONLY"
    if event_type == "natural_disaster":
        return "FINANCING_NEED"  # recovery financing, not just sympathy
    if event_type == "eu_regulatory_change":
        return "CAPEX_FINANCING" if direction == "negative" else "ADVISORY_ONLY"
        # negative = compliance cost, often capex (equipment/process changes);
        # not reliably distinguishable further without more structure than
        # this simulated event carries -- an honest limitation, not hidden
    if event_type == "sanctions_regulatory_change":
        return "RISK_REVIEW"  # compliance/counterparty exposure review, not
                               # a sales moment -- see conversation note on
                               # the separate "displacement opportunity"
                               # mechanism this does NOT yet model
    if event_type == "geopolitical_disruption":
        return "RISK_REVIEW"
    return "ADVISORY_ONLY"


def _load_transaction_rows(state_path: str) -> list[dict]:
    con = sqlite3.connect(state_path)
    df = pd.read_sql_query(
        """SELECT d.*, n.narrative_json FROM detections d
           LEFT JOIN narratives n ON d.detection_id = n.detection_id
           WHERE d.status = 'detected'""",
        con,
    )
    con.close()
    rows = []
    for _, r in df.iterrows():
        narrative = json.loads(r["narrative_json"]) if pd.notna(r["narrative_json"]) else None
        rows.append({
            "rank": r["rank"], "score": r["score"], "client_id": r["client_id"],
            "category": TRANSACTION_RULE_CATEGORY.get(r["rule_version"], "ADVISORY_ONLY"),
            "event_type": r["rule_version"],
            "recommended_action": narrative["suggested_action"] if narrative else "(no narrative generated)",
            "evidence_summary": f"{r['flagged_amount']:,.2f} flagged on {r['event_date']} "
                                f"(detection {r['detection_id']})",
            "event_date": r["event_date"], "detection_id": r["detection_id"],
            "narrative_source": narrative["narrative_source"] if narrative else "",
        })
    return rows


def _load_macro_rows(state_path: str) -> list[dict]:
    con = sqlite3.connect(state_path)
    df = pd.read_sql_query(
        """SELECT d.*, n.narrative_json FROM detections d
           LEFT JOIN narratives n ON d.detection_id = n.detection_id
           WHERE d.status = 'detected'""",
        con,
    )
    con.close()
    rows = []
    for _, r in df.iterrows():
        narrative = json.loads(r["narrative_json"]) if pd.notna(r["narrative_json"]) else None
        # macro detections don't have direction/headline/severity persisted
        # in the shared `detections` table (see external_events/
        # demo_scenario.py's mapping into that table's transaction-shaped
        # columns) -- pull them back out of the detection_id-embedded
        # event_id by re-reading the source events file once, joined below
        # in build_worklist() instead of here, to keep this function only
        # responsible for what's actually in the state store.
        rows.append({
            "rank": r["rank"], "score": r["score"], "client_id": r["client_id"],
            "event_type": r["rule_version"], "recommended_action": narrative["suggested_action"] if narrative else "(no narrative generated)",
            "event_date": r["event_date"], "detection_id": r["detection_id"],
            "narrative_source": narrative["narrative_source"] if narrative else "",
            "transaction_id": r["transaction_id"],  # this is actually event_id, see the mapping note above
        })
    return rows


def build_worklist(clients_csv_path: str, transaction_state_path: str | None = None,
                    macro_state_path: str | None = None,
                    external_events_csv_path: str | None = None) -> pd.DataFrame:
    clients = pd.read_csv(clients_csv_path)[
        ["client_id", "legal_name", "segment", "sector", "country", "relationship_manager_id"]
    ]

    all_rows = []
    if transaction_state_path:
        all_rows.extend(_load_transaction_rows(transaction_state_path))

    if macro_state_path and external_events_csv_path:
        events = pd.read_csv(external_events_csv_path).set_index("event_id")
        for row in _load_macro_rows(macro_state_path):
            event_id = row.pop("transaction_id")
            ev = events.loc[event_id]
            row["category"] = categorize_macro_event(ev["event_type"], ev["direction"])
            row["evidence_summary"] = (
                f"{ev['event_type']} (severity {ev['severity']}/5, {ev['direction']}) on "
                f"{row['event_date']}: \"{ev['headline']}\" [{ev['real_source_type']} -- "
                f"{ev['source_context']}]"
            )
            all_rows.append(row)

    if not all_rows:
        return pd.DataFrame(columns=WORKLIST_COLUMNS)

    df = pd.DataFrame(all_rows)
    df = df.merge(clients, on="client_id", how="left")
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1  # re-rank across the COMBINED worklist, not
                                 # each source's own internal rank
    return df[WORKLIST_COLUMNS]


def write_worklist_csv(df: pd.DataFrame, out_dir: str) -> str:
    from pathlib import Path
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(out_dir) / f"worklist_{ts}.csv"
    df.to_csv(out_path, index=False)
    return str(out_path)
