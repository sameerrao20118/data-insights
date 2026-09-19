"""
Detector coverage -- the systemic guard for the class of bug that let
TREASURY_OPPORTUNITY sit dark in the flagship pipeline indefinitely.

NOTE ON THE FILENAME: this file was first written as
`test_detector_liveness.py` and silently never ran -- "liveness"
contains "live", so the repo-wide `-k "not live"` convention
deselected it (434 passed / 13 deselected instead of 436 / 11). A
guard against silent regressions was itself silently skipped. Keep
"live" out of this file's name and its test names.

The failure it catches: a detector that is wired, tested, and green at
unit level, but whose threshold can never be crossed by the data the
product actually ships with -- so a whole revenue category silently
never appears on an RM's worklist. Unit tests can't catch this (they
construct their own fixtures that DO cross the threshold); only running
every registered detector against the real shipped dataset can.

This is deliberately an EXACT set comparison, not a subset check: a
detector that starts firing must be removed from DARK_BY_DESIGN, and a
detector that stops firing must fail. An allowlist that silently absorbs
new entries would recreate the bug it exists to prevent.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")

# A detector may only appear here WITH a reason that is about the data
# contract, never "it doesn't fire yet". Anything else is a live gap.
DARK_BY_DESIGN: dict[str, str] = {
    # Both found BY THIS TEST on first run, not previously known. Shared
    # root cause: data_generator/fdm/generate_fdm.py anchors these two
    # cohorts to END_DATE (2026-06-30) -- `spike` ramps utilisation only
    # in the final 30 days, and `maturity_soon`/fixed-rate ends land in
    # the final 89 days -- but every demo and test runs at
    # as_of = event_date + 90 = 2025-10-04, roughly nine months earlier.
    # So the cohorts exist and the detectors are correct; the generated
    # "interesting" events simply sit in the future relative to the
    # as-of the product actually evaluates at.
    #
    # Deliberately NOT fixed in the same change that fixed cash_buildup:
    # re-anchoring three cohorts at once would make it impossible to tell
    # which change produced which detection. Tracked as its own task.
    # R23: the FDM generator injects no single outsized credit (its
    # deposit story is the gradual cash_buildup ramp). The detector fires
    # on the legacy-schema book, where the generator does inject them --
    # tests/test_large_incoming_payment.py proves that end to end.
    "check_large_incoming_payment": "FDM generator injects no outsized single credit; fires on the legacy book (45 of 606 clients)",
    "check_facility_utilization": "utilisation spike cohort ramps only in the last 30 days before END_DATE; demo as_of is ~9 months earlier",
    "check_fixed_rate_expiry": "fixed-rate end dates cluster in the last 89 days before END_DATE; at demo as_of they are ~200+ days out, beyond the detector's horizon",
}


def _tool_name(tool) -> str:
    return getattr(tool, "tool_name", None) or getattr(tool, "__name__", str(tool))


def _statuses_for_every_detector() -> dict[str, dict[str, int]]:
    from agents.tools import (make_deposits_tools, make_exogenous_tools,
                              make_lending_tools, make_risk_tools)
    from datainsights.runtime import build_runtime
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource
    from external_events.exposure_qualifier import load_events

    rt = build_runtime("fdm_local")
    event = load_events(rt.event_source_path)[0]
    as_of = event.event_date + timedelta(days=90)
    canonical = CanonicalSource(rt.source, load_binding(rt.binding_name or "fdm"))

    tools = (make_deposits_tools(canonical, rt.rules, as_of)
             + make_lending_tools(canonical, rt.rules, as_of)
             + make_risk_tools(canonical, rt.rules, as_of)
             + make_exogenous_tools(canonical, rt.rules, event, as_of))

    party_ids = sorted(rt.source.party(as_of)["PRTY_ID"])
    counts: dict[str, dict[str, int]] = {_tool_name(t): {} for t in tools}
    for prty_id in party_ids:
        for tool in tools:
            result = tool(prty_id)
            status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
            bucket = counts[_tool_name(tool)]
            bucket[status] = bucket.get(status, 0) + 1
    return counts


@pytest.fixture(scope="module")
def statuses():
    return _statuses_for_every_detector()


def test_every_registered_detector_fires_on_the_shipped_dataset(statuses):
    """If this fails, either the shipped data can no longer exercise a
    detector, or a detector's thresholds drifted out of reach of it."""
    dark = {name for name, counts in statuses.items() if not counts.get("detected")}
    documented = set(DARK_BY_DESIGN)
    newly_dark = dark - documented
    assert not newly_dark, (
        f"detector(s) produced ZERO detections across the whole shipped book: "
        f"{sorted(newly_dark)}. Either the data generator no longer exercises them "
        f"or a threshold in config/rules.yaml is out of reach of the data. "
        f"Per-detector status counts: { {k: statuses[k] for k in sorted(newly_dark)} }"
    )
    resurrected = documented - dark
    assert not resurrected, (
        f"{sorted(resurrected)} now fire(s) but are still listed in DARK_BY_DESIGN -- "
        f"remove them from the allowlist so it keeps meaning something."
    )


def test_treasury_opportunity_is_reachable_end_to_end(statuses):
    """The specific regression: cash_buildup is the only detector that
    produces TREASURY_OPPORTUNITY (config/domains_fdm.yaml). If it never
    fires, that revenue category cannot appear on any worklist."""
    assert statuses["check_cash_buildup"].get("detected"), (
        "cash_buildup never fires -> TREASURY_OPPORTUNITY is unreachable. "
        f"statuses: {statuses['check_cash_buildup']}"
    )
