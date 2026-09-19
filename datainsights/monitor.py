"""
The clock -- R11's first trigger source. Reads the profile's `monitor:`
block (declared in every profile since Phase 0, read by nothing until
now): `enabled`, `interval_minutes`, `lookback_ref` (a dotted key into
config/rules.yaml naming the days a row may be old and still count as
"new", e.g. `cash_buildup.window_days`; None = no bound) and, through
datainsights/runs.py, `max_concurrent_runs`.

Micro-batch, not streaming: every tick is one incremental run_book().
An overlapping tick (a run still going) is skipped and logged, never
stacked. Run: python -m datainsights.monitor --profile fdm_local [--once]
"""

from __future__ import annotations

import time
from datetime import date

from datainsights.runs import RunOverlapError, run_book
from datainsights.runtime import active_profile, build_runtime


def lookback_days_from_ref(rules: dict, ref: str | None) -> int | None:
    if not ref:
        return None
    node = rules
    for part in ref.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"monitor.lookback_ref {ref!r} does not resolve in config/rules.yaml")
        node = node[part]
    return int(node)


def run_loop(profile_name: str = "fdm_local", *, once: bool = False, max_iterations: int | None = None,
             sleep=time.sleep, as_of_fn=date.today, log=print) -> list:
    """Returns the RunResults produced. `sleep`/`as_of_fn`/`log` are
    injectable so the loop is testable without waiting or a wall clock."""
    profile = active_profile(profile_name)
    if not profile.monitor.enabled and not once:
        raise RuntimeError(f"monitor.enabled is false for profile {profile_name!r} -- set it, or pass --once")
    lookback = lookback_days_from_ref(build_runtime(profile_name).rules, profile.monitor.lookback_ref)
    results = []
    iteration = 0
    while True:
        iteration += 1
        try:
            result = run_book(profile_name, as_of=as_of_fn(), incremental=True, lookback_days=lookback)
            results.append(result)
            log(f"[monitor] tick {iteration}: run {result.run_id} evaluated {len(result.evaluated_ids)}, "
                f"carried {len(result.carried_ids)}, {len(result.recommendations)} recommendations in {result.seconds:.1f}s")
        except RunOverlapError as e:
            log(f"[monitor] tick {iteration} skipped: {e}")
        if once or (max_iterations is not None and iteration >= max_iterations):
            return results
        sleep(profile.monitor.interval_minutes * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fdm_local")
    parser.add_argument("--once", action="store_true", help="one tick, then exit (ignores monitor.enabled)")
    args = parser.parse_args()
    run_loop(args.profile, once=args.once)


if __name__ == "__main__":
    main()
