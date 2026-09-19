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

docs/generalization_plan.md Phase 2 (R2): steps 1-3 above are no longer
per-event-type Python branches. `qualifies()` reads
config/event_types.yaml (via external_events/event_registry.py) for the
event's `match` predicates (steps 1-2, generalized) and `exposure.all_of`
composition (step 3, built from external_events/exposure_checks.py's
named-check library). Adding a new event type -- like fx_rate_move, the
second type proving this is genuinely declarative -- is a YAML edit, not
a new function here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from datainsights.semantic.canonical import CanonicalSource
from external_events import event_registry
from external_events.event_registry import EventRegistryError
from external_events.exposure_checks import CHECK_LIBRARY

# Base ExogenousEvent CSV columns -- anything else in a events CSV row
# becomes a payload field (load_events() below), so a new event type's
# extra columns need no change here.
_CORE_CSV_COLUMNS = {"event_id", "event_date", "event_type", "affected_country",
                    "affected_sector", "severity", "estimated_value_eur"}


@dataclass(frozen=True)
class ExogenousEvent:
    event_id: str
    event_date: date
    event_type: str
    affected_country: str
    affected_sector: str
    severity: int
    estimated_value_eur: float
    # Type-specific fields config/event_types.yaml's `payload:` block
    # declares (e.g. fx_rate_move's currency_pair/currency/pct_change) --
    # {} for a type with no payload fields (public_tender_award today).
    payload: dict = field(default_factory=dict)


def _resolve_field_ref(event: ExogenousEvent, ref):
    """`ref` is a value from config/event_types.yaml: "payload.<key>" ->
    event.payload[<key>]; a core ExogenousEvent attribute name ->
    getattr(event, ref); anything else (an int, a literal string that
    matches neither) is returned as-is -- a plain constant, not a
    reference."""
    if isinstance(ref, str) and ref.startswith("payload."):
        return event.payload.get(ref[len("payload."):])
    if isinstance(ref, str) and ref in _CORE_CSV_COLUMNS - {"event_id", "event_type"}:
        return getattr(event, ref)
    return ref


def _eval_predicate(predicate: dict, event: ExogenousEvent, canonical: CanonicalSource, prty_id: str) -> bool:
    party_field = predicate["field"]
    is_membership = "in" in predicate
    target = _resolve_field_ref(event, predicate["in"] if is_membership else predicate["equals"])
    if predicate.get("wildcard_if_empty") and not target:
        return True
    party = canonical.read("Party", party_id=prty_id)
    if party.empty or party_field not in party.columns:
        return False
    party_value = party.iloc[0][party_field]
    return (party_value in target) if is_membership else (party_value == target)


def sector_match(prty_id: str, event: ExogenousEvent, canonical: CanonicalSource) -> bool:
    """Step 1, kept as a named function for callers that only care about
    sector (tests, narrative code) -- now a lookup into this event
    type's own `match` predicates rather than a hardcoded branch. A type
    with no sector_code predicate (e.g. fx_rate_move) is vacuously true
    here; its own registered predicates are still enforced by
    qualifies()."""
    predicate = next((p for p in event_registry.spec(event.event_type).match
                      if p["field"] == "sector_code"), None)
    return True if predicate is None else _eval_predicate(predicate, event, canonical, prty_id)


def geography_match(prty_id: str, event: ExogenousEvent, canonical: CanonicalSource) -> bool:
    """Step 2, same pattern as sector_match for country_code."""
    predicate = next((p for p in event_registry.spec(event.event_type).match
                      if p["field"] == "country_code"), None)
    return True if predicate is None else _eval_predicate(predicate, event, canonical, prty_id)


def _resolve_check_kwargs(check_cfg: dict, event: ExogenousEvent) -> dict:
    return {k: _resolve_field_ref(event, v) for k, v in check_cfg.items() if k != "check"}


def _compute_magnitude(magnitude_spec: dict, event: ExogenousEvent, facts: dict) -> float:
    if "from_check" in magnitude_spec:
        value = facts.get(magnitude_spec["from_check"], {}).get(magnitude_spec["field"], 0.0)
    else:
        value = event.payload.get(magnitude_spec["from_payload"], 0.0)
    value = float(value or 0.0)
    if magnitude_spec.get("abs"):
        value = abs(value)
    lo, hi = magnitude_spec.get("clamp", [0.0, 1.0])
    return max(lo, min(hi, value))


def qualifies(prty_id: str, event: ExogenousEvent, canonical: CanonicalSource, rules: dict,
              as_at: date) -> tuple[bool, float]:
    """Full pipeline for whichever event type `event.event_type` names:
    every `match` predicate (steps 1-2) must pass, then every
    `exposure.all_of` check (step 3) must pass, all read from
    config/event_types.yaml. Returns (qualifies, magnitude). A client
    failing any match predicate never reaches step 3 -- and a client
    passing every match predicate but failing step 3 is exactly the
    decision record's 'negative case': same sector/country (or whatever
    this type matches on), no genuine exposure, must not qualify."""
    try:
        event_spec = event_registry.spec(event.event_type)
    except EventRegistryError:
        raise NotImplementedError(
            f"No exposure check implemented for event_type={event.event_type!r}. "
            f"Registered types: {sorted(event_registry.known_event_types())} -- see "
            f"config/event_types.yaml."
        )

    for predicate in event_spec.match:
        if not _eval_predicate(predicate, event, canonical, prty_id):
            return False, 0.0

    facts: dict[str, dict] = {}
    for check_cfg in event_spec.exposure["all_of"]:
        check_name = check_cfg["check"]
        fn = CHECK_LIBRARY[check_name]
        kwargs = _resolve_check_kwargs(check_cfg, event)
        passed, check_facts = fn(canonical, prty_id, as_at, rules=rules, **kwargs)
        if not passed:
            return False, 0.0
        facts[check_name] = check_facts

    magnitude = _compute_magnitude(event_spec.exposure["magnitude"], event, facts)
    return True, magnitude


def load_events(path: str) -> list[ExogenousEvent]:
    df = pd.read_csv(path)
    payload_columns = [c for c in df.columns if c not in _CORE_CSV_COLUMNS]
    events = []
    for _, row in df.iterrows():
        payload = {c: row[c] for c in payload_columns if pd.notna(row[c])}
        affected_country = row["affected_country"] if pd.notna(row["affected_country"]) else ""
        affected_sector = row["affected_sector"] if pd.notna(row["affected_sector"]) else ""
        events.append(ExogenousEvent(
            event_id=row["event_id"], event_date=date.fromisoformat(row["event_date"]),
            event_type=row["event_type"], affected_country=affected_country,
            affected_sector=affected_sector, severity=int(row["severity"]),
            estimated_value_eur=float(row["estimated_value_eur"]) if pd.notna(row.get("estimated_value_eur")) else 0.0,
            payload=payload,
        ))
    return events
