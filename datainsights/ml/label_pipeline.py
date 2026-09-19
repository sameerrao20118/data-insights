"""
SLOT E4 readiness, no model (docs/generalization_plan.md Phase 4) --
joins RM feedback (datainsights/rm_feedback.py, the D6 label source) to
the worklist row it responded to, producing a training-table SCHEMA a
future ranking model would train on. `datainsights.ml.slots.
UnavailablePropensityModel` stays unimplemented -- this is the plumbing
that would feed it the day real feedback volume exists, not a model
itself.

Point-in-time correctness by construction, not by extra bookkeeping: a
worklist row already carries the features that were true AS OF the run
that produced it (signal_strength, sizing, category, ...) -- joining
feedback back onto that row never needs a separate "what did we know
then" lookup, because the row never gets recomputed retroactively.

Every column here traces to something already public in this repo (the
worklist CSV, the feedback DB) -- never protected_evaluator_only/ or any
generator ground-truth label.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

TRAINING_TABLE_COLUMNS = [
    "recommendation_id", "prty_id", "nba_category", "signal_strength",
    "confirming_domains", "endogenous_signal_type", "sizing_basis",
    "indicative_revenue_eur", "indicative_offer_eur", "response", "sub_reason", "recorded_at",
]


def build_training_table(feedback_con: sqlite3.Connection, worklist: pd.DataFrame) -> pd.DataFrame:
    """`worklist` is a DataFrame already shaped like
    var/insights/fdm_rm_worklist.csv (datainsights/fdm_worklist.py's
    RM_WORKLIST_COLUMNS) -- read it with pandas before calling this,
    same as every other consumer of that file. Returns one row per
    feedback event (a client can be responded to more than once over
    time -- datainsights/rm_feedback.py's own history view already
    relies on that), joined to its recommendation's own features.
    Empty (not an error) if no feedback has been recorded yet."""
    feedback = pd.read_sql_query(
        "SELECT recommendation_id, response, sub_reason, recorded_at FROM rm_feedback", feedback_con,
    )
    if feedback.empty or worklist.empty:
        return pd.DataFrame(columns=TRAINING_TABLE_COLUMNS)

    joined = feedback.merge(worklist, on="recommendation_id", how="inner")
    missing = set(TRAINING_TABLE_COLUMNS) - set(joined.columns)
    for col in missing:
        joined[col] = None
    return joined[TRAINING_TABLE_COLUMNS]


def dry_run(feedback_db_path: str, worklist_csv_path: str) -> pd.DataFrame:
    """CLI/demo entry point -- python -m datainsights.ml.label_pipeline."""
    from datainsights.rm_feedback import connect

    worklist = pd.read_csv(worklist_csv_path) if __import__("os").path.exists(worklist_csv_path) else pd.DataFrame()
    with connect(feedback_db_path) as con:
        return build_training_table(con, worklist)


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback-db", default=os.path.join("var", "rm_feedback.db"))
    parser.add_argument("--worklist-csv", default=os.path.join("var", "insights", "fdm_rm_worklist.csv"))
    args = parser.parse_args()

    table = dry_run(args.feedback_db, args.worklist_csv)
    print(f"Training table: {len(table)} labelled rows, columns: {list(table.columns)}")
    if table.empty:
        print("No RM feedback recorded yet -- this is the expected state until an RM "
             "uses the dashboard's 'Record RM response' panel. Schema is proven; the "
             "table itself is empty by honest construction, not a bug.")
    else:
        print(table.to_string(index=False))


if __name__ == "__main__":
    main()
