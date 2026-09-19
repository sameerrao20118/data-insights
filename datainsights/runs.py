"""
Run records, the run lock and incremental (watermarked) whole-book runs --
R19 + R11 + R12 (docs/refactor_plan.md §3, §6g).

Before this, detection was pull-based: a human chose an as-of and ran
the whole book, every client, every time; nothing recorded that a run
had happened; two overlapping runs would have written over each other.

Now:
  * every run is a row in `runs` (one table, one file: var/runs.db by
    default, next to the profile's state db), started/finished/failed,
    with what it evaluated and the source high-water mark it saw;
  * a RunLock honours the profile's `monitor.max_concurrent_runs` -- a
    second run that would overlap raises RunOverlapError instead of
    corrupting state (a `running` row whose process is dead is treated
    as abandoned, never as a live lock);
  * run_book(..., incremental=True) re-evaluates only the clients with a
    canonical row newer than the previous run's high-water mark (R11),
    the clients a NEW extracted exogenous event qualifies (R12), and the
    clients whose carried recommendation is older than the longest
    detector cooldown; every other client's recommendation is carried
    forward from the previous run's stored copy. Detectors are untouched.

Non-goal: no streaming. A micro-batch on a clock (datainsights/monitor.py)
is the right shape for a bank worklist.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from agents.orchestrator import evaluate_book
from datainsights.correlation.dedupe import dedupe
from datainsights.correlation.hypothesis import Recommendation
from datainsights.fdm_worklist import build_rm_worklist, write_rm_digest, write_rm_worklist_csv
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from external_events.exposure_qualifier import load_events, qualifies
from external_events.extracted_event_store import read_extracted_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACTED_EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm_extracted", "extracted_events.csv")

# canonical concept -> the column whose max is that concept's high-water mark
HIGH_WATER_COLUMNS = {
    "BalanceObservation": "observed_at",
    "Transaction": "posted_at",
    "Account": "valid_from",
    "Party": "valid_from",
    "RiskGradeVersion": "valid_from",
    "CollateralValuation": "valid_from",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, profile TEXT NOT NULL, as_of TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
    host TEXT, pid INTEGER, incremental INTEGER NOT NULL DEFAULT 0,
    n_clients INTEGER, n_evaluated INTEGER, n_recommendations INTEGER,
    high_water TEXT, events_high_water TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS run_recommendations (
    run_id TEXT NOT NULL, prty_id TEXT NOT NULL, recommendation TEXT NOT NULL,
    PRIMARY KEY (run_id, prty_id)
);
"""


class RunOverlapError(RuntimeError):
    pass


@dataclass
class RunResult:
    run_id: str
    profile: str
    as_of: date
    incremental: bool
    evaluated_ids: list[str]
    carried_ids: list[str]
    recommendations: list[Recommendation]
    worklist: pd.DataFrame
    seconds: float
    reasons: dict[str, str] = field(default_factory=dict)  # prty_id -> why it was re-evaluated
    out_paths: dict[str, str] = field(default_factory=dict)


def runs_db_path(rt) -> str:
    return os.path.join(os.path.dirname(rt.state_db_path), "runs.db")


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.executescript(_SCHEMA)
    return con


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RunLock:
    """Honours monitor.max_concurrent_runs (R19). Enter: registers this
    run as `running` unless that many live runs already exist for the
    profile. Exit: marks finished or failed. A `running` row whose pid
    is dead on this host is marked `abandoned` and does not count."""

    def __init__(self, con: sqlite3.Connection, profile: str, as_of: date, *, max_concurrent: int,
                 incremental: bool):
        self.con, self.profile, self.as_of = con, profile, as_of
        self.max_concurrent, self.incremental = max_concurrent, incremental
        self.run_id = f"{profile}:{as_of.isoformat()}:{uuid.uuid4().hex[:8]}"

    def __enter__(self) -> "RunLock":
        host = socket.gethostname()
        with self.con:  # one transaction: count + insert
            self.con.execute("BEGIN IMMEDIATE")
            rows = self.con.execute("SELECT run_id, host, pid FROM runs WHERE profile=? AND status='running'",
                                    (self.profile,)).fetchall()
            live = 0
            for run_id, run_host, pid in rows:
                if run_host == host and not _pid_alive(pid):
                    self.con.execute("UPDATE runs SET status='abandoned', finished_at=? WHERE run_id=?",
                                     (_now(), run_id))
                else:
                    live += 1
            if live >= self.max_concurrent:
                raise RunOverlapError(
                    f"{live} run(s) already running for profile {self.profile!r} "
                    f"(monitor.max_concurrent_runs={self.max_concurrent}); refusing to overlap.")
            self.con.execute(
                "INSERT INTO runs (run_id, profile, as_of, started_at, status, host, pid, incremental) "
                "VALUES (?,?,?,?,'running',?,?,?)",
                (self.run_id, self.profile, self.as_of.isoformat(), _now(), host, os.getpid(), int(self.incremental)))
        return self

    def __exit__(self, exc_type, exc, tb):
        with self.con:
            if exc is None:
                self.con.execute("UPDATE runs SET status='finished', finished_at=? WHERE run_id=?", (_now(), self.run_id))
            else:
                self.con.execute("UPDATE runs SET status='failed', finished_at=?, error=? WHERE run_id=?",
                                 (_now(), f"{type(exc).__name__}: {exc}", self.run_id))
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def last_finished_run(con: sqlite3.Connection, profile: str) -> dict | None:
    row = con.execute("SELECT run_id, as_of, high_water, events_high_water, finished_at FROM runs "
                      "WHERE profile=? AND status='finished' ORDER BY finished_at DESC LIMIT 1", (profile,)).fetchone()
    if row is None:
        return None
    return {"run_id": row[0], "as_of": date.fromisoformat(row[1]), "high_water": json.loads(row[2] or "{}"),
            "events_high_water": row[3], "finished_at": row[4]}


# ---- watermark ----------------------------------------------------------

def high_water(canonical: CanonicalSource, as_of: date) -> dict[str, str]:
    """concept -> max timestamp seen (ISO), for every concept with a
    watermark column the binding provides. Rows after as_of are ignored
    -- a watermark must never see the future either."""
    out: dict[str, str] = {}
    for concept, col in HIGH_WATER_COLUMNS.items():
        if not canonical.available(concept):
            continue
        df = canonical.versions(concept)
        if df.empty or col not in df.columns:
            continue
        ts = pd.to_datetime(df[col], errors="coerce")
        ts = ts[ts <= pd.Timestamp(as_of)]
        if ts.notna().any():
            out[concept] = ts.max().isoformat()
    return out


def changed_parties(canonical: CanonicalSource, previous: dict[str, str], as_of: date,
                    lookback_days: int | None = None) -> dict[str, str]:
    """prty_id -> reason, for every client with any canonical row newer
    than the previous high-water mark (R11). `lookback_days` bounds how
    old a row can be and still count as new (monitor.lookback_ref)."""
    reasons: dict[str, str] = {}
    accounts = canonical.versions("Account") if canonical.available("Account") else pd.DataFrame()
    acct_to_party = dict(zip(accounts.get("account_id", []), accounts.get("party_id", [])))
    floor = pd.Timestamp(as_of - timedelta(days=lookback_days)) if lookback_days else None
    for concept, col in HIGH_WATER_COLUMNS.items():
        if not canonical.available(concept):
            continue
        df = canonical.versions(concept)
        if df.empty or col not in df.columns:
            continue
        ts = pd.to_datetime(df[col], errors="coerce")
        mask = ts <= pd.Timestamp(as_of)
        prev = previous.get(concept)
        if prev is not None:
            mask &= ts > pd.Timestamp(prev)
        if floor is not None:
            mask &= ts >= floor
        new = df[mask]
        if new.empty:
            continue
        if "party_id" in new.columns:
            parties = new["party_id"].dropna().unique()
        elif "account_id" in new.columns:
            parties = {acct_to_party.get(a) for a in new["account_id"].unique()} - {None}
        else:
            continue
        for p in parties:
            reasons.setdefault(str(p), f"new {concept} rows since {prev or 'the beginning'}")
    return reasons


# ---- recommendation persistence ----------------------------------------

_TUPLE_FIELDS = ("confirming_domains", "response_actions")


def _rec_to_json(rec: Recommendation) -> str:
    return json.dumps(asdict(rec), default=str)


def _rec_from_json(text: str) -> Recommendation:
    d = json.loads(text)
    for k in _TUPLE_FIELDS:
        if k in d and d[k] is not None:
            d[k] = tuple(d[k])
    return Recommendation(**d)


def _store_recommendations(con: sqlite3.Connection, run_id: str, recs: list[Recommendation]) -> None:
    with con:
        con.executemany("INSERT OR REPLACE INTO run_recommendations (run_id, prty_id, recommendation) VALUES (?,?,?)",
                        [(run_id, r.prty_id, _rec_to_json(r)) for r in recs])


def _load_recommendations(con: sqlite3.Connection, run_id: str) -> dict[str, list[Recommendation]]:
    out: dict[str, list[Recommendation]] = {}
    for prty_id, text in con.execute("SELECT prty_id, recommendation FROM run_recommendations WHERE run_id=?", (run_id,)):
        out.setdefault(prty_id, []).append(_rec_from_json(text))
    return out


# ---- the run ------------------------------------------------------------

def _pick_event(rt, events_path: str | None):
    """The newest extracted event, else the profile's feed. Returns
    (event, events_high_water_iso, all_extracted)."""
    extracted = read_extracted_events(events_path) if events_path else []
    if extracted:
        # the store is append-only; the last row is the newest
        return extracted[-1], _events_high_water(events_path), extracted
    if rt.event_source_path and os.path.exists(rt.event_source_path):
        return load_events(rt.event_source_path)[0], None, []
    return None, None, []


def _events_high_water(events_path: str) -> str | None:
    if not events_path or not os.path.exists(events_path):
        return None
    df = pd.read_csv(events_path)
    return str(df["extracted_at"].max()) if "extracted_at" in df.columns and len(df) else None


def _new_extracted_events(events_path: str | None, previous_high_water: str | None) -> list:
    if not events_path or not os.path.exists(events_path):
        return []
    df = pd.read_csv(events_path)
    if "extracted_at" in df.columns and previous_high_water:
        df = df[df["extracted_at"].astype(str) > str(previous_high_water)]
    if df.empty:
        return []
    events = read_extracted_events(events_path)
    keep = set(df["event_id"])
    return [e for e in events if e.event_id in keep]


def _max_cooldown_days(rules: dict) -> int:
    return max((int(v.get("cooldown_days", 0)) for v in rules.values() if isinstance(v, dict)), default=0)


def run_book(profile_name: str = "fdm_local", *, as_of: date | None = None, incremental: bool = True,
             lookback_days: int | None = None, runs_db: str | None = None,
             events_path: str | None = EXTRACTED_EVENTS_PATH, data_dir_override: str | None = None,
             write_outputs: bool = True) -> RunResult:
    """One whole-book run, recorded and locked. `incremental=True` (the
    default) re-evaluates only what changed since the previous finished
    run of this profile; the first run of a profile is always full."""
    t0 = time.perf_counter()
    rt = build_runtime(profile_name, data_dir_override=data_dir_override)
    binding_name = rt.binding_name or "fdm"
    canonical = CanonicalSource(rt.source, load_binding(binding_name))
    event, events_hw, _ = _pick_event(rt, events_path)
    if as_of is None:
        as_of = (event.event_date + timedelta(days=90)) if event else date.today()

    con = connect(runs_db or runs_db_path(rt))
    previous = last_finished_run(con, profile_name) if incremental else None
    with RunLock(con, profile_name, as_of, max_concurrent=rt.profile.monitor.max_concurrent_runs,
                 incremental=bool(previous)) as lock:
        parties = canonical.read("Party", as_at=as_of)
        all_ids = sorted(parties["party_id"])
        reasons: dict[str, str] = {}
        carried: dict[str, list[Recommendation]] = {}

        if previous is None:
            evaluated_ids = all_ids
            reasons = {p: "full run" for p in all_ids}
        else:
            reasons = changed_parties(canonical, previous["high_water"], as_of, lookback_days)
            # R12: a NEW extracted event enqueues exactly the clients whose
            # own data confirms exposure -- not everyone in its sector.
            for new_event in _new_extracted_events(events_path, previous.get("events_high_water")):
                for p in all_ids:
                    if p in reasons:
                        continue
                    ok, _ = qualifies(p, new_event, canonical, rt.rules, as_of)
                    if ok:
                        reasons[p] = f"new exogenous event {new_event.event_id} qualifies"
            prior = _load_recommendations(con, previous["run_id"])
            stale_after = _max_cooldown_days(rt.rules)
            carried_age = (as_of - previous["as_of"]).days
            for p in all_ids:
                if p in reasons:
                    continue
                recs = prior.get(p, [])
                if recs and carried_age > stale_after:
                    # the longest detector cooldown has expired since the
                    # carried recommendation was made -- look again
                    reasons[p] = f"carried recommendation is {carried_age}d old, past the longest cooldown ({stale_after}d)"
                else:
                    carried[p] = recs
            evaluated_ids = sorted(p for p in all_ids if p in reasons)

        evaluations = evaluate_book(evaluated_ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event,
                                    binding_name=binding_name) if evaluated_ids else []
        fresh = [e.recommendation for e in evaluations if e.recommendation]
        carried_recs = [r for recs in carried.values() for r in recs]
        recommendations = dedupe(fresh + carried_recs)
        worklist = build_rm_worklist(recommendations, rt.source, rt.rules, as_of, binding_name=binding_name)

        _store_recommendations(con, lock.run_id, recommendations)
        with con:
            con.execute("UPDATE runs SET n_clients=?, n_evaluated=?, n_recommendations=?, high_water=?, "
                        "events_high_water=? WHERE run_id=?",
                        (len(all_ids), len(evaluated_ids), len(recommendations),
                         json.dumps(high_water(canonical, as_of)), events_hw, lock.run_id))
        out_paths = {}
        if write_outputs:
            out_paths["worklist"] = write_rm_worklist_csv(worklist, os.path.join(rt.out_dir, "fdm_rm_worklist.csv"))
            out_paths["digest"] = write_rm_digest(worklist, os.path.join(rt.out_dir, "fdm_rm_digest.md"), as_of,
                                                  run_notes=f"run {lock.run_id}: {len(evaluated_ids)} of {len(all_ids)} "
                                                            f"clients re-evaluated, {len(carried)} carried forward")
    con.close()
    return RunResult(run_id=lock.run_id, profile=profile_name, as_of=as_of, incremental=previous is not None,
                     evaluated_ids=evaluated_ids, carried_ids=sorted(carried), recommendations=recommendations,
                     worklist=worklist, seconds=time.perf_counter() - t0, reasons=reasons, out_paths=out_paths)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="One recorded, locked, incremental whole-book run (R11/R19).")
    parser.add_argument("--profile", default="fdm_local")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--full", action="store_true", help="re-evaluate every client, ignore the watermark")
    args = parser.parse_args()
    result = run_book(args.profile, as_of=date.fromisoformat(args.as_of) if args.as_of else None,
                      incremental=not args.full)
    print(f"run {result.run_id}: as_of {result.as_of}, {len(result.evaluated_ids)} evaluated, "
          f"{len(result.carried_ids)} carried, {len(result.recommendations)} recommendations, {result.seconds:.2f}s")
    for p, why in list(result.reasons.items())[:10]:
        print(f"  {p}: {why}")
    for k, v in result.out_paths.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
