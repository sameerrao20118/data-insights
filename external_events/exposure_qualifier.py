"""
Exposure qualification -- docs/decision_record.md's "why exposure
qualification is the whole trick":

    Step 1  sector match       event NACE  -> PARTY_DEMOGRAPHIC   (as at event date)
    Step 2  geography match    event region -> PARTY_LOCATOR      (as at event date)
    Step 3  EXPOSURE QUALIFICATION  <- the part that matters
            Does the client actually hold exposure this event affects?
              tender award   -> capacity to deliver? existing WC headroom?

"Without step 3 this is a mailing list. Sector-plus-country alone produces
a broadcast. Requiring demonstrable exposure from the client's own FDM
data is what makes it a signal an RM can defend."

Explicit documented functions, no ML -- matching the decision record's
Slot E1-before-E2 ordering (explicit fn first, learnable challenger
later).

Simplification, disclosed: "capacity to deliver / existing WC headroom"
for a tender award is approximated here as "this client's deposit account
already shows a genuine revenue_pattern_change detection" (structural
growth in incoming payments) rather than a real working-capital-ratio
calculation, which is not modeled in this dataset. This is the one
detector the decision record's own Agent Signal Map table pairs with a
tender award ("Revenue pattern change ... Tender award (TED API) ...
FINANCING_NEED ... Structural revenue growth" -- docs/fdm_reference.md).
A real deployment would check actual working-capital ratios; this is an
honest, disclosed placeholder for that, not a claim of doing real
financial capacity analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from datainsights.sources.fdm_local import FdmLocalSource
from detection_engine import revenue_pattern_change


@dataclass(frozen=True)
class ExogenousEvent:
    event_id: str
    event_date: date
    event_type: str
    affected_country: str
    affected_sector: str
    severity: int
    estimated_value_eur: float


def sector_match(prty_id: str, event: ExogenousEvent, source: FdmLocalSource) -> bool:
    """Step 1. affected_sector is empty string == applies to all sectors,
    matching the existing legacy external_macro_event.py convention for
    a wildcard."""
    if not event.affected_sector:
        return True
    demo = source.party_demographic()
    row = demo[demo["PRTY_ID"] == prty_id]
    if row.empty:
        return False
    return row.iloc[0]["NACE_SECTION_CD"] == event.affected_sector


def geography_match(prty_id: str, event: ExogenousEvent, source: FdmLocalSource) -> bool:
    """Step 2. affected_country empty string == applies broadly."""
    if not event.affected_country:
        return True
    loc = source.party_locator()
    row = loc[loc["PRTY_ID"] == prty_id]
    if row.empty:
        return False
    return row.iloc[0]["COUNTRY_CD"] == event.affected_country


def _tender_award_exposure(prty_id: str, source: FdmLocalSource, rules: dict, as_at: date) -> tuple[bool, float]:
    """Step 3 for public_tender_award: does the client show a genuine
    revenue_pattern_change signal (the disclosed capacity-to-deliver
    proxy documented in this module's docstring)?"""
    party_agreement, _ = source.read_entity("PARTY_AGREEMENT", allow_unbounded=True)
    agreements = source.agreement(as_at)
    dep_ids = agreements.loc[agreements["AGRMNT_TYP_CD"] == "DEP", "AGRMNT_ID"]
    dep_ids_for_party = party_agreement.loc[
        (party_agreement["PRTY_ID"] == prty_id) & (party_agreement["AGRMNT_ID"].isin(dep_ids)),
        "AGRMNT_ID",
    ]
    if dep_ids_for_party.empty:
        return False, 0.0

    cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(rules)
    events = source.financial_event(as_at.replace(year=as_at.year - 1), as_at)
    events = events[events["AGRMNT_ID_TRN_ACCT"].isin(dep_ids_for_party)].rename(
        columns={"AGRMNT_ID_TRN_ACCT": "AGRMNT_ID"})
    if events.empty:
        return False, 0.0
    events = events.copy()
    events["PRTY_ID"] = prty_id
    result = revenue_pattern_change.detect(events, cfg, "exposure_qualifier")
    detected = result[result["status"] == "detected"]
    if detected.empty:
        return False, 0.0
    magnitude = min(1.0, abs(float(detected.iloc[-1]["change_pct"])))
    return True, magnitude


EXPOSURE_CHECKS = {
    "public_tender_award": _tender_award_exposure,
}


def qualifies(prty_id: str, event: ExogenousEvent, source: FdmLocalSource, rules: dict,
              as_at: date) -> tuple[bool, float]:
    """Full 3-step pipeline. Returns (qualifies, magnitude). A client
    failing sector or geography match never reaches step 3 -- and a
    client passing sector+geography but failing step 3 is exactly the
    decision record's stated 'negative case': same sector/country, no
    genuine exposure, must not qualify."""
    if not sector_match(prty_id, event, source):
        return False, 0.0
    if not geography_match(prty_id, event, source):
        return False, 0.0
    check = EXPOSURE_CHECKS.get(event.event_type)
    if check is None:
        raise NotImplementedError(
            f"No exposure check implemented for event_type={event.event_type!r}. "
            f"Only 'public_tender_award' is in scope this pass -- see "
            f"docs/decision_record.md Tab 6 Slot C ('open' status for the "
            f"other five real exogenous sources)."
        )
    return check(prty_id, source, rules, as_at)


def load_events(path: str) -> list[ExogenousEvent]:
    df = pd.read_csv(path)
    return [
        ExogenousEvent(
            event_id=row["event_id"], event_date=date.fromisoformat(row["event_date"]),
            event_type=row["event_type"], affected_country=row["affected_country"] or "",
            affected_sector=row["affected_sector"] or "", severity=int(row["severity"]),
            estimated_value_eur=float(row["estimated_value_eur"]),
        )
        for _, row in df.iterrows()
    ]
