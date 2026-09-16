"""
FDM-shaped exogenous event generator -- the decision record's chosen
Phase 1 exemplar: "public tender award (TED) + matching large incoming
payment... highest value, existing production route" (docs/decision_record.md
Tab 5, Phase 1), refined against the Agent Signal Map's actual signal
pairing captured in docs/fdm_reference.md: "Revenue pattern change |
EVENT_FINANCIAL frequency/amount trend | Event | Tender award (TED API) |
FINANCING_NEED | Structural revenue growth" -- i.e. the endogenous
counterpart is revenue_pattern_change (built in M3), not a ported
large_incoming_payment (that detector stays legacy-schema-only; porting
it was explicitly out of scope for this pass).

Reuses the same output shape as datainsights/sources/external_event_source
.py's REQUIRED_COLUMNS -- one event type only (public_tender_award), the
one already defined in external_events/simulate_external_events.py's
EVENT_TYPE_CATALOG, reused here rather than redefined.

Picks a real (NACE_SECTION_CD, COUNTRY_CD) combination from the already-
generated FDM dataset that has both a party WITH a genuine
revenue_pattern_change detection (the positive/exposed case) and at least
one party WITHOUT one (the negative case docs/decision_record.md calls
"the real test": same sector/country, no genuine exposure, must not
qualify).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from detection_engine import revenue_pattern_change  # noqa: E402

REQUIRED_COLUMNS = [
    "event_id", "event_date", "event_type", "source_name", "real_source_type",
    "source_context", "affected_country", "affected_sector", "direction", "severity",
    "estimated_value_eur", "headline", "description",
]

REVENUE_RULES = {
    "fdm_rule_version": "fdm.v1",
    "revenue_pattern_change": {
        "window_days": 60, "min_credits_per_window": 3, "min_change_pct": 0.5, "cooldown_days": 45,
    },
}


def _find_exposed_and_unexposed_party(fdm_dir: str):
    # All 5 of these are `domain: kernel` entities (config/entities_fdm.yaml)
    kernel_dir = os.path.join(fdm_dir, "kernel")
    party_demo = pd.read_csv(os.path.join(kernel_dir, "party_demographic.csv"))
    party_loc = pd.read_csv(os.path.join(kernel_dir, "party_locator.csv"))
    agreement = pd.read_csv(os.path.join(kernel_dir, "agreement.csv"))
    party_agreement = pd.read_csv(os.path.join(kernel_dir, "party_agreement.csv"))
    events = pd.read_csv(os.path.join(kernel_dir, "event_financial.csv"))

    dep_agreements = agreement.loc[agreement["AGRMNT_TYP_CD"] == "DEP", "AGRMNT_ID"]
    ev = events[events["AGRMNT_ID_TRN_ACCT"].isin(dep_agreements)].merge(
        party_agreement, left_on="AGRMNT_ID_TRN_ACCT", right_on="AGRMNT_ID")
    ev = ev.drop(columns=["AGRMNT_ID"]).rename(columns={"AGRMNT_ID_TRN_ACCT": "AGRMNT_ID"})

    cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(REVENUE_RULES)
    detections = revenue_pattern_change.detect(ev, cfg, "gen_events")
    detected = detections[detections["status"] == "detected"]
    # earliest detected event_date per party -- used to date the tender
    # award BEFORE the revenue growth it's meant to explain (award ->
    # delivery -> revenue increase), not floating disconnected from it
    earliest_detection_date = detected.groupby("prty_id")["event_date"].min().to_dict()
    detected_ids = set(earliest_detection_date.keys())

    demo_loc = party_demo.merge(party_loc, on="PRTY_ID")
    for (sector, country), grp in demo_loc.groupby(["NACE_SECTION_CD", "COUNTRY_CD"]):
        ids = set(grp["PRTY_ID"])
        exposed = ids & detected_ids
        unexposed = ids - detected_ids
        if exposed and unexposed:
            exposed_id = sorted(exposed)[0]
            return sector, country, exposed_id, sorted(unexposed)[0], earliest_detection_date[exposed_id]

    raise RuntimeError(
        "could not find any (sector, country) combo with both an exposed and "
        "unexposed party -- try a different --seed or check generate_fdm.py's "
        "step_change probability"
    )


def generate(fdm_dir: str, out_dir: str) -> pd.DataFrame:
    sector, country, exposed_prty, unexposed_prty, detected_event_date = \
        _find_exposed_and_unexposed_party(fdm_dir)
    # Tender award precedes the revenue growth it's meant to explain --
    # the delivery/payment lag is what creates the working-capital need
    # (docs/decision_record.md's own hypothesis text), not a coincidence
    # of timing.
    event_date = date.fromisoformat(detected_event_date) - timedelta(days=30)
    row = {
        "event_id": str(uuid.uuid4()),
        "event_date": event_date.isoformat(),
        "event_type": "public_tender_award",
        "source_name": "TED award notice (simulated)",
        "real_source_type": "TED (Tenders Electronic Daily) API",
        "source_context": "Structured award notices with country/sector/value fields -- "
                           "no extraction step needed.",
        "affected_country": country,
        "affected_sector": sector,
        "direction": "positive",
        "severity": 4,
        "estimated_value_eur": 3_200_000,
        "headline": f"Public infrastructure tender awarded in NACE section {sector} sector, {country}",
        "description": "Simulated TED award notice for the FDM-shaped exogenous exemplar "
                        "(docs/decision_record.md Phase 1). Not a real tender.",
    }
    df = pd.DataFrame([row], columns=REQUIRED_COLUMNS)
    os.makedirs(out_dir, exist_ok=True)
    df.to_csv(os.path.join(out_dir, "tender_events.csv"), index=False)
    print(f"Event targets NACE section '{sector}' / country '{country}'")
    print(f"  exposed party (has revenue_pattern_change):   {exposed_prty}")
    print(f"  unexposed party (same sector/country, no signal): {unexposed_prty}")
    print(f"Written to {out_dir}/tender_events.csv")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fdm-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "output_fdm"))
    parser.add_argument("--out-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "external_events", "output_fdm"))
    args = parser.parse_args()
    generate(args.fdm_dir, args.out_dir)


if __name__ == "__main__":
    main()
