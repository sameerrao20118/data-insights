"""
Outcome backtest with a virtual clock -- R18 (docs/refactor_plan.md §6i).

The question a credit committee actually asks: "were the recommendations
we made as of T right, judged by what the relationship managers recorded
AFTER T?" evaluation/evaluate.py answers a different question (synthetic
labels, a development diagnostic). This module runs the deterministic
pipeline as of each T -- nothing after T is visible to any detector, and
an exogenous event dated after T is not in scope either -- then joins RM
feedback (datainsights/rm_feedback.py, the D6 label source) recorded
strictly after T, by the stable recommendation_id, and reports per
category: how many were recommended, how many got any response, how many
were "Customer Engaged" (the hit), and the hit rate among responded.

This is the acceptance test for SLOT E4: a propensity model earns its
place only by beating these hit rates on held-out T's -- not by fitting.
Today the feedback store is nearly empty, so the honest output of this
tool on the shipped data is "0 outcomes recorded"; the machinery is what
R18 delivers, the numbers arrive with the labels.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd

from agents.orchestrator import evaluate_book
from datainsights.correlation.dedupe import dedupe
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from external_events.exposure_qualifier import load_events
from external_events.extracted_event_store import read_extracted_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FEEDBACK_DB = os.path.join(REPO_ROOT, "var", "rm_feedback.db")
EXTRACTED_EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm_extracted", "extracted_events.csv")

HIT = "Customer Engaged"
REPORT_COLUMNS = ["as_of", "nba_category", "n_recommended", "n_with_outcome", "n_engaged", "n_not_appropriate",
                  "n_remind_later", "hit_rate", "outcome_coverage"]


def _events_visible_at(rt, as_of: date, events_path: str | None):
    """Virtual clock for the exogenous side too: only events dated on or
    before T exist at T. The most recent such event is the run's event."""
    events = []
    if events_path and os.path.exists(events_path):
        events += read_extracted_events(events_path)
    if rt.event_source_path and os.path.exists(rt.event_source_path):
        events += load_events(rt.event_source_path)
    visible = [e for e in events if e.event_date <= as_of]
    return max(visible, key=lambda e: e.event_date) if visible else None


def _feedback_after(con: sqlite3.Connection, as_of: date) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT recommendation_id, prty_id, response, recorded_at FROM rm_feedback", con)
    if df.empty:
        return df
    recorded = pd.to_datetime(df["recorded_at"], utc=True, errors="coerce")
    cutoff = pd.Timestamp(datetime(as_of.year, as_of.month, as_of.day), tz="UTC") + pd.Timedelta(days=1)
    df = df[recorded >= cutoff]  # strictly after the as-of DAY -- nothing recorded on or before T counts
    # one outcome per recommendation: the latest response wins
    return df.sort_values("recorded_at").drop_duplicates("recommendation_id", keep="last")


def backtest_one(profile_name: str, as_of: date, *, feedback_db: str = DEFAULT_FEEDBACK_DB,
                 events_path: str | None = EXTRACTED_EVENTS_PATH, data_dir_override: str | None = None,
                 party_limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (per-category report rows for this T, the joined detail)."""
    rt = build_runtime(profile_name, data_dir_override=data_dir_override)
    binding = rt.binding_name or "fdm"
    canonical = CanonicalSource(rt.source, load_binding(binding))
    ids = sorted(canonical.read("Party", as_at=as_of)["party_id"])
    if party_limit:
        ids = ids[:party_limit]
    event = _events_visible_at(rt, as_of, events_path)
    book = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event, binding_name=binding)
    recs = dedupe([e.recommendation for e in book if e.recommendation])
    made = pd.DataFrame([{"recommendation_id": r.recommendation_id, "prty_id": r.prty_id,
                          "nba_category": r.nba_category} for r in recs],
                        columns=["recommendation_id", "prty_id", "nba_category"])
    if os.path.exists(feedback_db):
        with sqlite3.connect(feedback_db) as con:
            outcomes = _feedback_after(con, as_of)
    else:
        outcomes = pd.DataFrame(columns=["recommendation_id", "prty_id", "response", "recorded_at"])
    detail = made.merge(outcomes[["recommendation_id", "response", "recorded_at"]], on="recommendation_id", how="left")
    detail["as_of"] = as_of.isoformat()

    rows = []
    for cat, grp in detail.groupby("nba_category", sort=True):
        responded = grp["response"].notna().sum()
        engaged = (grp["response"] == HIT).sum()
        rows.append({
            "as_of": as_of.isoformat(), "nba_category": cat, "n_recommended": len(grp),
            "n_with_outcome": int(responded), "n_engaged": int(engaged),
            "n_not_appropriate": int((grp["response"] == "Not Appropriate").sum()),
            "n_remind_later": int((grp["response"] == "Remind Me Later").sum()),
            "hit_rate": round(engaged / responded, 3) if responded else None,
            "outcome_coverage": round(responded / len(grp), 3) if len(grp) else None,
        })
    return pd.DataFrame(rows, columns=REPORT_COLUMNS), detail


def backtest(profile_name: str, as_of_dates: list[date], **kw) -> pd.DataFrame:
    frames = [backtest_one(profile_name, t, **kw)[0] for t in as_of_dates]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=REPORT_COLUMNS)


def date_range(start: date, end: date, step_days: int) -> list[date]:
    out, t = [], start
    while t <= end:
        out.append(t)
        t += timedelta(days=step_days)
    return out


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fdm_local")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--feedback-db", default=DEFAULT_FEEDBACK_DB)
    args = parser.parse_args()
    dates = date_range(date.fromisoformat(args.start), date.fromisoformat(args.end), args.step_days)
    report = backtest(args.profile, dates, feedback_db=args.feedback_db)
    total_outcomes = int(report["n_with_outcome"].sum()) if len(report) else 0
    print(f"Outcome backtest -- profile {args.profile}, {len(dates)} as-of dates, virtual clock (nothing after T is visible)")
    print(report.to_string(index=False) if len(report) else "no recommendations at any T")
    if total_outcomes == 0:
        print("\n0 outcomes recorded after any T: hit rates are undefined until RMs record responses "
              "(dashboard feedback panel -> var/rm_feedback.db). The machinery is real; the numbers need labels.")


if __name__ == "__main__":
    main()
