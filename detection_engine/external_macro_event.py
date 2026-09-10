"""
Detector: External Macro Event -> affected clients, by sector/country match.

This is the "matching" stage of the extraction -> matching -> review
pattern in docs/objective.md. It does NOT extract structure from
unstructured text (that's a future NewsEventSource/SocialFeedEventSource's
job, upstream of this) and it does NOT decide what to do about a match
(that's ranking + narrative + human review, downstream). All this does is:
given an already-structured event (sector, country, direction, severity)
and the client book (sector, country, segment), find clients plausibly
affected, and record that as evidence -- nothing more.

Deliberately narrow claim: "this event's sector/country overlaps this
client's" is NOT "this event affects this client's business," which is NOT
"this client needs this specific product." Each step down that chain needs
more evidence than this detector has. The narrative layer's caveats say so
explicitly (see datainsights/narrative/macro_narrator.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

DETECTION_COLUMNS = [
    "detection_id", "rule_version", "client_id", "event_id", "event_date",
    "detection_as_of", "event_type", "source_name", "real_source_type",
    "source_context", "affected_country", "affected_sector", "direction", "severity",
    "headline", "description", "status",
]

RULE_VERSION = "external_macro_event.v1"


@dataclass(frozen=True)
class MacroDetectorConfig:
    min_severity: int = 3          # ignore routine low-severity noise by default
    cooldown_days: int = 45        # a client shouldn't get re-flagged daily for
                                    # the same ongoing event window


def detect(events: pd.DataFrame, clients: pd.DataFrame, config: MacroDetectorConfig,
           run_id: str) -> pd.DataFrame:
    """clients must have columns: client_id, sector, country. events must
    satisfy datainsights.sources.external_event_source.REQUIRED_COLUMNS."""
    events = events[events["severity"] >= config.min_severity].copy()
    if events.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for _, ev in events.iterrows():
        matched = clients
        # empty affected_country/affected_sector = wildcard (applies broadly),
        # per the ExternalEventSource contract -- not a missing value
        if ev["affected_country"]:
            matched = matched[matched["country"] == ev["affected_country"]]
        if ev["affected_sector"]:
            matched = matched[matched["sector"] == ev["affected_sector"]]

        for _, client in matched.iterrows():
            rows.append({
                "detection_id": f"{RULE_VERSION}:{ev['event_id']}:{client['client_id']}",
                "rule_version": RULE_VERSION,
                "client_id": client["client_id"],
                "event_id": ev["event_id"],
                "event_date": ev["event_date"],
                "detection_as_of": now,
                "event_type": ev["event_type"],
                "source_name": ev["source_name"],
                "real_source_type": ev["real_source_type"],
                "source_context": ev["source_context"],
                "affected_country": ev["affected_country"],
                "affected_sector": ev["affected_sector"],
                "direction": ev["direction"],
                "severity": int(ev["severity"]),
                "headline": ev["headline"],
                "description": ev["description"],
                "status": "detected",
            })

    return pd.DataFrame(rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: MacroDetectorConfig) -> pd.DataFrame:
    """Same idea as the transaction detector's cooldown: don't let the same
    (client, event_type) pair re-surface as a fresh 'detected' row within
    cooldown_days of an earlier one -- an ongoing energy-price event
    shouldn't generate a new active recommendation every routine update."""
    if detections.empty:
        return detections
    out = detections.copy()
    out["event_date_dt"] = pd.to_datetime(out["event_date"])

    for (client_id, event_type), grp in out.groupby(["client_id", "event_type"]):
        grp_sorted = grp.sort_values("event_date_dt")
        last_active_date = None
        for idx, row in grp_sorted.iterrows():
            if last_active_date is not None and \
                    (row["event_date_dt"] - last_active_date).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
            else:
                last_active_date = row["event_date_dt"]

    return out.drop(columns=["event_date_dt"])
