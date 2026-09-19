"""
Deterministic detectors exposed as Strands @tool functions. Tools do no
LLM work themselves -- each one fetches the minimal data slice for one
client through CanonicalSource (docs/generalization_plan.md Phase 1),
translates it back to the exact column names the existing detect()
functions already require, and runs that unmodified detect(). The
domain agent's job is only to decide which tools to call and narrate
what came back; detection logic itself is exactly what tests/test_*.py
already verify -- and those detector files and their tests are
UNCHANGED by this module reading through CanonicalSource instead of
FdmLocalSource directly. Swapping the binding (fdm -> legacy -> a new
schema) changes what data these tools see; it does not change a single
line in detection_engine/.

Factories (make_deposits_tools, make_lending_tools) bind a canonical
source, rules config, and as-of date once, so the LLM-facing tool
signature stays minimal (just prty_id) rather than asking the model to
pass through DataFrame-shaped arguments.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from strands import tool

from agents.domain_registry import DomainSpec, register
from datainsights.semantic.canonical import CanonicalSource
from detection_engine import (
    cash_buildup,
    collateral_coverage_drop,
    dormancy,
    facility_maturity_approaching,
    facility_utilization_spike,
    fixed_rate_expiry,
    rating_downgrade,
    revenue_pattern_change,
    large_incoming_payment,
)

# -- canonical -> detector-expected column names ----------------------------
# Every detector in detection_engine/ still requires these exact physical-
# style names (REQUIRED_COLUMNS in each file) -- that vocabulary is now the
# repo's stable "canonical" naming, unchanged by this rewire. What changed
# is HOW the data arrives here: through CanonicalSource + a binding, not a
# hardcoded FdmLocalSource method call. See docs/generalization_plan.md
# Phase 1 and config/bindings/{fdm,legacy}.yaml.
def _accounts(canonical: CanonicalSource, prty_id: str, product_classes: list[str], as_of: date) -> pd.DataFrame:
    """Every account of the given canonical product_class(es) for this
    client, in CANONICAL column names -- detectors read those directly
    now (R1, docs/refactor_plan.md). Empty (not an error) if the concept
    has no rows for this client. A field the binding doesn't provide
    (e.g. fixed_rate_end_date under legacy, which has no mortgages)
    arrives as a column of NaT, never a KeyError."""
    df = canonical.read("Account", as_at=as_of, party_id=prty_id)
    if df.empty:
        return df
    out = df[df["product_class"].isin(product_classes)].copy()
    if "fixed_rate_end_date" not in out.columns:
        out["fixed_rate_end_date"] = pd.NaT
    return out


def _currency(account_row, default: str = "EUR") -> str:
    """R17: the account's own currency (semantic Account.currency, which a
    binding sets from a column or a const), defaulting to EUR only when
    the binding provides nothing."""
    try:
        cur = account_row.get("currency")
    except AttributeError:
        return default
    return str(cur).upper() if cur is not None and str(cur) not in ("", "nan", "None") else default


def _balances(canonical: CanonicalSource, account_id: str, start: date, end: date) -> pd.DataFrame:
    df = canonical.read("BalanceObservation", account_id=account_id)
    if df.empty:
        return df
    ts = pd.to_datetime(df["observed_at"])
    return df[(ts >= pd.Timestamp(start)) & (ts <= pd.Timestamp(end))].reset_index(drop=True)


def _transactions(canonical: CanonicalSource, account_id: str, start: date, end: date) -> pd.DataFrame:
    df = canonical.read("Transaction", account_id=account_id)
    if df.empty:
        return df
    ts = pd.to_datetime(df["posted_at"])
    return df[(ts >= pd.Timestamp(start)) & (ts <= pd.Timestamp(end))].reset_index(drop=True)


def make_deposits_tools(canonical: CanonicalSource, rules: dict, as_of: date) -> list:
    cash_buildup_cfg = cash_buildup.DetectorConfig.from_rules_dict(rules)
    dormancy_cfg = dormancy.DetectorConfig.from_rules_dict(rules)
    revenue_cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(rules)
    lip_cfg = large_incoming_payment.DetectorConfig.from_rules_dict(rules)

    @tool
    def check_cash_buildup(prty_id: str) -> dict:
        """Check whether this client's deposit balance shows a sustained
        buildup over the trailing window. Returns the detection facts, or
        {'status': 'not_detected'} / {'status': 'no_deposit_account'}."""
        dep = _accounts(canonical, prty_id, ["deposit"], as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        cur_by_acct = {a["account_id"]: _currency(a) for _, a in dep.iterrows()}
        frames = []
        for _, agr in dep.iterrows():
            bal = _balances(canonical, agr["account_id"],
                            as_of - timedelta(days=cash_buildup_cfg.window_days * 2), as_of)
            bal = bal.copy()
            bal["party_id"] = prty_id
            bal["currency"] = _currency(agr)
            frames.append(cash_buildup.detect(bal, cash_buildup_cfg, "agent"))
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        detected = result[result["status"] == "detected"] if not result.empty else result
        if detected.empty:
            return {"status": "not_detected"}
        row = detected.sort_values("event_date").iloc[-1]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "increase_pct": float(row["increase_pct"]),
                "current_balance": float(row["current_balance"]),
                "prior_balance": float(row["prior_balance"]), "currency": cur_by_acct.get(row["agrmnt_id"], "EUR"),
                "evidence_ref": cash_buildup.to_signal(row).evidence_ref, "baseline": row["baseline"]}

    @tool
    def check_dormancy(prty_id: str) -> dict:
        """Check whether this client's deposit account has gone dormant
        (no transactions for longer than the configured threshold)."""
        dep = _accounts(canonical, prty_id, ["deposit"], as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        agrmnt_ids = list(dep["account_id"])
        per_account = [_transactions(canonical, aid, as_of - timedelta(days=365 * 2), as_of)
                       for aid in agrmnt_ids]
        per_account = [e for e in per_account if not e.empty]
        events = pd.concat(per_account, ignore_index=True) if per_account else pd.DataFrame(
            columns=["account_id", "posted_at", "amount", "direction"])
        events = events.copy()
        events["party_id"] = prty_id
        result = dormancy.detect(events, agrmnt_ids, dormancy_cfg, "agent", as_of)
        result = result[result["status"] == "detected"] if "status" in result.columns else result
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        acct = dep[dep["account_id"] == row["agrmnt_id"]]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "currency": _currency(acct.iloc[0]) if not acct.empty else _currency(dep.iloc[0]),
                "days_since_last_event": int(row["days_since_last_event"]),
                "last_event_date": row["last_event_date"],
                "evidence_ref": dormancy.to_signal(row).evidence_ref}

    @tool
    def check_revenue_pattern_change(prty_id: str) -> dict:
        """Check whether this client's deposit account shows a structural
        change in incoming (credit) transaction pattern -- amount or
        frequency -- versus its own recent history."""
        dep = _accounts(canonical, prty_id, ["deposit"], as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        cur_by_acct = {a["account_id"]: _currency(a) for _, a in dep.iterrows()}
        frames = []
        for _, agr in dep.iterrows():
            aid = agr["account_id"]
            events = _transactions(canonical, aid,
                                   as_of - timedelta(days=revenue_cfg.window_days * 3), as_of)
            events = events.copy()
            events["party_id"] = prty_id
            events["currency"] = _currency(agr)
            frames.append(revenue_pattern_change.detect(events, revenue_cfg, "agent"))
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        result = result[result["status"] == "detected"] if not result.empty else result
        if result.empty:
            return {"status": "not_detected"}
        row = result.sort_values("event_date").iloc[-1]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "change_pct": float(row["change_pct"]),
                "recent_mean_amount": float(row["recent_mean_amount"]),
                "prior_mean_amount": float(row["prior_mean_amount"]), "currency": cur_by_acct.get(row["agrmnt_id"], "EUR"),
                "evidence_ref": revenue_pattern_change.to_signal(row).evidence_ref, "baseline": row["baseline"]}

    @tool
    def check_large_incoming_payment(prty_id: str) -> dict:
        """Check whether a credit landed on this client's deposit account
        well above their own trailing baseline (median + k*MAD over the
        prior window) -- a temporary cash surplus worth a treasury
        conversation. Returns the detection facts or {'status': 'not_detected'}."""
        dep = _accounts(canonical, prty_id, ["deposit"], as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        cur_by_acct = {a["account_id"]: _currency(a) for _, a in dep.iterrows()}
        frames = []
        for _, agr in dep.iterrows():
            events = _transactions(canonical, agr["account_id"],
                                   as_of - timedelta(days=lip_cfg.baseline_window_days + lip_cfg.cooldown_days), as_of)
            if events.empty:
                continue
            events = events.copy()
            events["party_id"] = prty_id
            if "currency" not in events.columns or events["currency"].isna().all():
                events["currency"] = _currency(agr)
            frames.append(large_incoming_payment.apply_cooldown(
                large_incoming_payment.detect(events, lip_cfg, "agent"), lip_cfg))
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        detected = result[result["status"] == "detected"] if not result.empty else result
        if detected.empty:
            return {"status": "not_detected"}
        row = detected.sort_values("event_date").iloc[-1]
        row = row.copy(); row["magnitude_saturation_at"] = lip_cfg.magnitude_saturation_at
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "transaction_id": row["transaction_id"], "flagged_amount": float(row["flagged_amount"]),
                "baseline_median": float(row["baseline_median"]), "baseline_n": int(row["baseline_n"]),
                "baseline_mad": float(row["baseline_mad"]) if row["baseline_mad"] is not None else 0.0,
                "currency": cur_by_acct.get(row["agrmnt_id"], row["currency"]),
                "magnitude_saturation_at": lip_cfg.magnitude_saturation_at,
                "evidence_ref": large_incoming_payment.to_signal(row).evidence_ref}

    return [check_cash_buildup, check_dormancy, check_revenue_pattern_change, check_large_incoming_payment]


def make_lending_tools(canonical: CanonicalSource, rules: dict, as_of: date) -> list:
    util_cfg = facility_utilization_spike.DetectorConfig.from_rules_dict(rules)
    maturity_cfg = facility_maturity_approaching.DetectorConfig.from_rules_dict(rules)
    fixed_rate_cfg = fixed_rate_expiry.DetectorConfig.from_rules_dict(rules)
    coverage_cfg = collateral_coverage_drop.DetectorConfig.from_rules_dict(rules)

    @tool
    def check_facility_utilization(prty_id: str) -> dict:
        """Check whether this client's credit facility (loan or overdraft)
        is drawn above the configured utilization threshold."""
        fac = _accounts(canonical, prty_id, ["facility"], as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        cur_by_acct = {a["account_id"]: _currency(a) for _, a in fac.iterrows()}
        rows = []
        for _, agr in fac.iterrows():
            bal = _balances(canonical, agr["account_id"], as_of - timedelta(days=7), as_of)
            bal["party_id"] = prty_id
            bal["original_limit"] = agr["original_limit"]
            bal["currency"] = _currency(agr)
            rows.append(facility_utilization_spike.detect(bal, util_cfg, "agent"))
        result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        if result.empty:
            return {"status": "not_detected"}
        row = result.sort_values("event_date").iloc[-1]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "utilization_pct": float(row["utilization_pct"]),
                "drawn_amount": float(row["drawn_amount"]), "orig_limit": float(row["orig_limit"]),
                "currency": cur_by_acct.get(row["agrmnt_id"], "EUR"),
                "evidence_ref": facility_utilization_spike.to_signal(row).evidence_ref}

    @tool
    def check_facility_maturity(prty_id: str) -> dict:
        """Check whether any of this client's credit facilities are
        approaching their close/maturity date."""
        fac = _accounts(canonical, prty_id, ["facility"], as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        result = facility_maturity_approaching.detect(fac, maturity_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        acct = fac[fac["account_id"] == row["agrmnt_id"]]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "currency": _currency(acct.iloc[0]) if not acct.empty else "EUR",
                "close_date": row["close_date"], "days_to_close": int(row["days_to_close"]),
                "orig_limit": (float(row["orig_limit"])
                               if row["orig_limit"] is not None and pd.notna(row["orig_limit"]) else None),
                "evidence_ref": facility_maturity_approaching.to_signal(row).evidence_ref}

    @tool
    def check_fixed_rate_expiry(prty_id: str) -> dict:
        """Check whether this client's mortgage fixed-rate period is
        approaching expiry."""
        mortgages = _accounts(canonical, prty_id, ["mortgage"], as_of)
        if mortgages.empty:
            return {"status": "no_mortgage"}
        mortgages = mortgages.copy()
        mortgages["party_id"] = prty_id
        result = fixed_rate_expiry.detect(mortgages, fixed_rate_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        acct = mortgages[mortgages["account_id"] == row["agrmnt_id"]]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "currency": _currency(acct.iloc[0]) if not acct.empty else "EUR",
                "fixed_rate_end_date": row["fixed_rate_end_date"],
                "days_to_expiry": int(row["days_to_expiry"]),
                "evidence_ref": fixed_rate_expiry.to_signal(row).evidence_ref}

    @tool
    def check_collateral_coverage(prty_id: str) -> dict:
        """Check whether collateral coverage on this client's credit
        facilities has dropped below the required threshold, having
        previously been adequate. Reports 'not_available_under_this_binding'
        (not a crash) if the active schema binding has no collateral data
        at all -- e.g. the legacy schema."""
        if not canonical.available("CollateralValuation"):
            return {"status": "not_available_under_this_binding",
                    "reason": canonical.unavailable_reason("CollateralValuation")}
        fac = _accounts(canonical, prty_id, ["facility"], as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        # collateral_coverage_drop needs the FULL history (not just as-at
        # current) to tell "dropped" from "always thin" -- CanonicalSource's
        # CollateralValuation binding already carries the full effective-
        # dated history plus the joined original_limit, so no manual
        # multi-table merge is needed here any more.
        all_values = canonical.read("CollateralValuation")
        all_values = all_values[all_values["account_id"].isin(fac["account_id"])]
        if all_values.empty:
            return {"status": "no_collateral"}
        merged = all_values
        merged["party_id"] = prty_id
        result = collateral_coverage_drop.detect(merged, coverage_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        acct = fac[fac["account_id"] == row["agrmnt_id"]]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"],
                "currency": _currency(acct.iloc[0]) if not acct.empty else "EUR",
                "cltrl_item_id": row["cltrl_item_id"], "event_date": row["event_date"],
                "current_coverage_pct": float(row["current_coverage_pct"]),
                "prior_max_coverage_pct": float(row["prior_max_coverage_pct"]),
                "evidence_ref": collateral_coverage_drop.to_signal(row).evidence_ref}

    return [check_facility_utilization, check_facility_maturity,
            check_fixed_rate_expiry, check_collateral_coverage]


def make_exogenous_tools(canonical: CanonicalSource, rules: dict, event, as_of: date) -> list:
    """Exogenous domain, tool-calling only -- exposure QUALIFICATION
    itself stays deterministic Python (external_events/exposure_qualifier
    .py), per docs/decision_record.md's "no autonomous multi-step agent;
    correlation is a deterministic rules engine" constraint. This tool
    exposes that deterministic function to an agent exactly like the
    Deposits/Lending tools expose a detector -- the LLM orchestrates and
    narrates, it does not decide exposure itself.

    `event` is an external_events.exposure_qualifier.ExogenousEvent,
    bound once per event the way `as_of` is bound once per run."""
    from external_events.exposure_qualifier import qualifies

    @tool
    def check_exogenous_exposure(prty_id: str) -> dict:
        """Check whether this client has genuine exposure to a specific
        external event (sector match, geography match, and a real
        confirming signal from their own data) -- not just a broad
        sector/country broadcast."""
        qualified, magnitude = qualifies(prty_id, event, canonical, rules, as_of)
        if not qualified:
            return {"status": "not_detected"}
        return {
            "status": "detected",
            "event_type": event.event_type,
            "event_date": event.event_date.isoformat(),
            "affected_sector": event.affected_sector,
            "affected_country": event.affected_country,
            "estimated_value_eur": event.estimated_value_eur,
            "exposure_magnitude": round(magnitude, 4),
            "evidence_ref": f"EXOGENOUS:{event.event_id}:prty={prty_id}",
        }

    return [check_exogenous_exposure]


def make_risk_tools(canonical: CanonicalSource, rules: dict, as_of: date) -> list:
    """A4 (docs/agentic_plan.md): the first domain wired through the
    registry entirely after M7. Only rating_downgrade is wired -- see
    that detector's own module docstring for why pd_migration stays
    unregistered (needs PARTY_METRIC, not in any binding's contract)."""
    rating_downgrade_cfg = rating_downgrade.DetectorConfig.from_rules_dict(rules)

    @tool
    def check_rating_downgrade(prty_id: str) -> dict:
        """Check whether this client's credit risk grade has worsened by
        at least the configured number of notches within the lookback
        window, versus the grade valid at the start of that window.
        Reports 'not_available_under_this_binding' (not a crash) if the
        active schema has no risk-grade history at all."""
        if not canonical.available("RiskGradeVersion"):
            return {"status": "not_available_under_this_binding",
                    "reason": canonical.unavailable_reason("RiskGradeVersion")}
        versions = canonical.versions("RiskGradeVersion", party_id=prty_id)
        if versions.empty:
            return {"status": "no_party_record"}
        versions = versions
        result = rating_downgrade.detect(versions, rating_downgrade_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        return {"status": "detected", "event_date": row["event_date"],
                "prior_grade_cd": row["prior_grade_cd"], "prior_grade_val": int(row["prior_grade_val"]),
                "current_grade_cd": row["current_grade_cd"], "current_grade_val": int(row["current_grade_val"]),
                "notches": int(row["notches"]), "grade_effective_date": row["grade_effective_date"],
                "evidence_ref": rating_downgrade.to_signal(row).evidence_ref}

    return [check_rating_downgrade]


# One register() call per domain -- the whole "code" half of adding a
# domain (see docs/adding_a_new_domain.md and agents/domain_registry.py's
# docstring for the "data" half, config/domains_fdm.yaml). Nothing in
# agents/orchestrator.py, agents/domain_agent.py, or
# datainsights/correlation/hypothesis.py needs to change to pick these up.
register(DomainSpec(
    name="deposits",
    make_tools=make_deposits_tools,
    detector_by_tool={
        "check_cash_buildup": cash_buildup,
        "check_dormancy": dormancy,
        "check_revenue_pattern_change": revenue_pattern_change,
        "check_large_incoming_payment": large_incoming_payment,
    },
))
register(DomainSpec(
    name="lending",
    make_tools=make_lending_tools,
    detector_by_tool={
        "check_facility_utilization": facility_utilization_spike,
        "check_facility_maturity": facility_maturity_approaching,
        "check_fixed_rate_expiry": fixed_rate_expiry,
        "check_collateral_coverage": collateral_coverage_drop,
    },
))
register(DomainSpec(
    name="exogenous",
    make_tools=make_exogenous_tools,
    # No detector_by_tool: exogenous evidence isn't a detection_engine
    # Signal -- agents/orchestrator.py reads check_exogenous_exposure's
    # evidence directly into assemble()'s exogenous kwargs instead.
    detector_by_tool={},
))
register(DomainSpec(
    name="risk",
    make_tools=make_risk_tools,
    detector_by_tool={"check_rating_downgrade": rating_downgrade},
))
