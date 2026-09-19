"""
Named exposure-check library (docs/generalization_plan.md Phase 2, R2)
-- the functions config/event_types.yaml's `exposure.all_of` entries
reference by name. Each check answers one question against a single
client's OWN canonical data ("does this client actually hold exposure
this event affects?", per docs/decision_record.md's step-3 discussion in
external_events/exposure_qualifier.py) and returns
`(passed: bool, facts: dict)` -- `facts` carries whatever numeric detail
the event type's `magnitude` spec (or a future narrative) might want,
never a business decision itself.

register_exposure_check(name, fn) lets bespoke Python join the library
for a check no declarative composition can express -- the same
extension pattern agents/domain_registry.py's DomainSpec uses for
detectors, applied here to exposure checks.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from datainsights.semantic.canonical import CanonicalSource


def has_account(canonical: CanonicalSource, prty_id: str, as_at: date, *,
                product_class: str, **_kwargs) -> tuple[bool, dict]:
    """Does this client hold an account of the given canonical
    product_class (deposit/facility/mortgage/other)?"""
    accounts = canonical.read("Account", as_at=as_at, party_id=prty_id)
    if accounts.empty or "product_class" not in accounts.columns:
        return False, {}
    match = accounts[accounts["product_class"] == product_class]
    return not match.empty, {"account_count": len(match)}


def recent_signal(canonical: CanonicalSource, prty_id: str, as_at: date, *,
                  signal_type: str, within_days: int, rules: dict, **_kwargs) -> tuple[bool, dict]:
    """Has detection_engine's `signal_type` detector actually fired for
    this client's own data within the last `within_days`? The disclosed
    capacity-to-deliver proxy documented in
    external_events/exposure_qualifier.py's module docstring -- carried
    over unchanged from before this check library existed, now reusable
    by any event type rather than hardcoded to public_tender_award.

    Only `revenue_pattern_change` is wired this pass -- it is the one
    Transaction-grain detector the decision record's own Agent Signal Map
    pairs with an exogenous event (docs/fdm_reference.md). Extending this
    to a Balance- or Account-grain detector (cash_buildup, dormancy, the
    lending detectors) is real, straightforward, undone work: each needs
    its own frame assembly here, the same way agents/tools.py's `_accounts`/
    `_balances`/`_transactions` helpers differ per detector family."""
    if signal_type != "revenue_pattern_change":
        raise NotImplementedError(
            f"recent_signal check: signal_type={signal_type!r} not wired -- only "
            f"'revenue_pattern_change' is implemented this pass, see this function's docstring."
        )
    from detection_engine import revenue_pattern_change

    accounts = canonical.read("Account", as_at=as_at, party_id=prty_id)
    deposits = accounts[accounts["product_class"] == "deposit"] if not accounts.empty else accounts
    if deposits.empty:
        return False, {}

    frames = []
    for account_id in deposits["account_id"]:
        tx = canonical.read("Transaction", account_id=account_id)
        if not tx.empty:
            frames.append(tx)
    if not frames:
        return False, {}
    events = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(events["posted_at"])
    window_start = pd.Timestamp(as_at) - pd.Timedelta(days=within_days)
    events = events[(ts >= window_start) & (ts <= pd.Timestamp(as_at))].copy()
    if events.empty:
        return False, {}
    events["party_id"] = prty_id

    cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(rules)
    result = revenue_pattern_change.detect(events, cfg, "exposure_qualifier")
    detected = result[result["status"] == "detected"]
    if detected.empty:
        return False, {}
    return True, {"change_pct": abs(float(detected.iloc[-1]["change_pct"]))}


def currency_activity(canonical: CanonicalSource, prty_id: str, as_at: date, *,
                      currency: str, within_days: int, min_transactions: int = 1,
                      **_kwargs) -> tuple[bool, dict]:
    """Does this client's own transaction history show at least
    `min_transactions` postings in `currency` within the last
    `within_days`? The deciding evidence for an FX-move event: sector/
    country match alone would broadcast to every client in the affected
    country, exactly the "mailing list" failure mode
    exposure_qualifier.py's docstring warns against for tender awards."""
    accounts = canonical.read("Account", as_at=as_at, party_id=prty_id)
    if accounts.empty:
        return False, {}
    window_start = pd.Timestamp(as_at) - pd.Timedelta(days=within_days)
    count = 0
    for account_id in accounts["account_id"]:
        tx = canonical.read("Transaction", account_id=account_id)
        if tx.empty or "currency" not in tx.columns:
            continue
        ts = pd.to_datetime(tx["posted_at"])
        window = tx[(ts >= window_start) & (ts <= pd.Timestamp(as_at)) & (tx["currency"] == currency)]
        count += len(window)
    return count >= min_transactions, {"transaction_count": count}


CHECK_LIBRARY = {
    "has_account": has_account,
    "recent_signal": recent_signal,
    "currency_activity": currency_activity,
}


def register_exposure_check(name: str, fn) -> None:
    """Adds (or replaces) a named check -- for a check no declarative
    `all_of` composition can express, matching agents/domain_registry.py's
    DomainSpec extension pattern. `fn` must accept
    (canonical, prty_id, as_at, **kwargs) and return (bool, dict)."""
    CHECK_LIBRARY[name] = fn
