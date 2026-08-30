"""
Minimal local status view (project instructions section 10.1: "Provide a
minimal local status view or report; a complex dashboard is optional").
Reports last successful run, watermark, active detection count, and
narrative cache stats -- from the local SQLite state store only.
"""

from __future__ import annotations

import sqlite3
import sys


def status(state_path: str = "var/state.sqlite") -> None:
    con = sqlite3.connect(state_path)
    last_run = con.execute(
        "SELECT run_id, started_at, finished_at, mode, profile, status, notes "
        "FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not last_run:
        print("No runs recorded yet.")
        return
    run_id, started, finished, mode, profile, run_status, notes = last_run
    n_active = con.execute("SELECT COUNT(*) FROM detections WHERE status='detected'").fetchone()[0]
    n_narratives = con.execute("SELECT COUNT(*) FROM narratives").fetchone()[0]
    n_runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    con.close()

    print(f"Last run:        {run_id}")
    print(f"  mode/profile:  {mode} / {profile}")
    print(f"  started:       {started}")
    print(f"  finished:      {finished or '(still running or crashed without finishing)'}")
    print(f"  status:        {run_status}")
    print(f"Total runs recorded: {n_runs}")
    print(f"Active detections (current): {n_active}")
    print(f"Cached narratives: {n_narratives}")
    print()
    print("This is a manual/replay POC, not 24/7 monitoring -- there is no "
          "background process. A stopped process simply means no new runs "
          "happen until you invoke one again; state persists between runs.")


if __name__ == "__main__":
    status(sys.argv[1] if len(sys.argv) > 1 else "var/state.sqlite")
