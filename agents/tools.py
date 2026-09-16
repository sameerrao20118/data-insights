"""
Deterministic detectors exposed as Strands @tool functions. Tools do no
LLM work themselves -- each one fetches the minimal data slice for one
client from FdmLocalSource, runs the existing tested detect() function
unmodified, and returns a small JSON-safe dict. The domain agent's job is
only to decide which tools to call and narrate what came back; detection
logic itself is exactly what tests/test_*.py already verify.

Factories (make_deposits_tools, make_lending_tools) bind a data source,
rules config, and as-of date once, so the LLM-facing tool signature stays
minimal (just prty_id) rather than asking the model to pass through
DataFrame-shaped arguments.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from strands import tool

from agents.domain_registry import DomainSpec, register
from datainsights.domain_registry import product_codes
from datainsights.sources.fdm_local import FdmLocalSource
from detection_engine import (
    cash_buildup,
    collateral_coverage_drop,
    dormancy,
    facility_maturity_approaching,
    facility_utilization_spike,
    fixed_rate_expiry,
    rating_downgrade,
    revenue_pattern_change,
)


def _party_agreements(source: FdmLocalSource, prty_id: str, typ_codes: list[str], as_of: date) -> pd.DataFrame:
    """Agreements for this party, scoped to the given type codes, with
    PRTY_ID attached as a column -- AGREEMENT itself has no PRTY_ID (that
    lives in PARTY_AGREEMENT), but every detector that consumes this
    output requires PRTY_ID per config/entities_fdm.yaml's contract."""
    agreements = source.agreement(as_of)
    party_agreement, _ = source.read_entity("PARTY_AGREEMENT", allow_unbounded=True)
    ids = party_agreement.loc[party_agreement["PRTY_ID"] == prty_id, "AGRMNT_ID"]
    scoped = agreements[agreements["AGRMNT_ID"].isin(ids) & agreements["AGRMNT_TYP_CD"].isin(typ_codes)].copy()
    scoped["PRTY_ID"] = prty_id
    return scoped


def make_deposits_tools(source: FdmLocalSource, rules: dict, as_of: date) -> list:
    cash_buildup_cfg = cash_buildup.DetectorConfig.from_rules_dict(rules)
    dormancy_cfg = dormancy.DetectorConfig.from_rules_dict(rules)
    revenue_cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(rules)
    deposit_codes = product_codes("deposits", "deposit")

    @tool
    def check_cash_buildup(prty_id: str) -> dict:
        """Check whether this client's deposit balance shows a sustained
        buildup over the trailing window. Returns the detection facts, or
        {'status': 'not_detected'} / {'status': 'no_deposit_account'}."""
        dep = _party_agreements(source, prty_id, deposit_codes, as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        agrmnt_id = dep.iloc[0]["AGRMNT_ID"]
        bal = source.daily_balance(as_of - timedelta(days=cash_buildup_cfg.window_days * 2), as_of)
        bal = bal[bal["AGRMNT_ID"] == agrmnt_id].copy()
        bal["PRTY_ID"] = prty_id
        result = cash_buildup.detect(bal, cash_buildup_cfg, "agent")
        detected = result[result["status"] == "detected"]
        if detected.empty:
            return {"status": "not_detected"}
        row = detected.iloc[-1]
        return {"status": "detected", "agrmnt_id": agrmnt_id, "event_date": row["event_date"],
                "increase_pct": float(row["increase_pct"]),
                "current_balance": float(row["current_balance"]),
                "prior_balance": float(row["prior_balance"]),
                "evidence_ref": cash_buildup.to_signal(row).evidence_ref, "baseline": row["baseline"]}

    @tool
    def check_dormancy(prty_id: str) -> dict:
        """Check whether this client's deposit account has gone dormant
        (no transactions for longer than the configured threshold)."""
        dep = _party_agreements(source, prty_id, deposit_codes, as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        agrmnt_id = dep.iloc[0]["AGRMNT_ID"]
        events = source.financial_event(as_of - timedelta(days=365 * 2), as_of)
        events = events[events["AGRMNT_ID_TRN_ACCT"] == agrmnt_id].rename(
            columns={"AGRMNT_ID_TRN_ACCT": "AGRMNT_ID"})
        events["PRTY_ID"] = prty_id
        result = dormancy.detect(events, [agrmnt_id], dormancy_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        return {"status": "detected", "agrmnt_id": agrmnt_id, "event_date": row["event_date"],
                "days_since_last_event": int(row["days_since_last_event"]),
                "last_event_date": row["last_event_date"],
                "evidence_ref": dormancy.to_signal(row).evidence_ref}

    @tool
    def check_revenue_pattern_change(prty_id: str) -> dict:
        """Check whether this client's deposit account shows a structural
        change in incoming (credit) transaction pattern -- amount or
        frequency -- versus its own recent history."""
        dep = _party_agreements(source, prty_id, deposit_codes, as_of)
        if dep.empty:
            return {"status": "no_deposit_account"}
        agrmnt_id = dep.iloc[0]["AGRMNT_ID"]
        events = source.financial_event(as_of - timedelta(days=revenue_cfg.window_days * 3), as_of)
        events = events[events["AGRMNT_ID_TRN_ACCT"] == agrmnt_id].rename(
            columns={"AGRMNT_ID_TRN_ACCT": "AGRMNT_ID"})
        events["PRTY_ID"] = prty_id
        result = revenue_pattern_change.detect(events, revenue_cfg, "agent")
        result = result[result["status"] == "detected"]
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[-1]
        return {"status": "detected", "agrmnt_id": agrmnt_id, "event_date": row["event_date"],
                "change_pct": float(row["change_pct"]),
                "recent_mean_amount": float(row["recent_mean_amount"]),
                "prior_mean_amount": float(row["prior_mean_amount"]),
                "evidence_ref": revenue_pattern_change.to_signal(row).evidence_ref, "baseline": row["baseline"]}

    return [check_cash_buildup, check_dormancy, check_revenue_pattern_change]


def make_lending_tools(source: FdmLocalSource, rules: dict, as_of: date) -> list:
    util_cfg = facility_utilization_spike.DetectorConfig.from_rules_dict(rules)
    maturity_cfg = facility_maturity_approaching.DetectorConfig.from_rules_dict(rules)
    fixed_rate_cfg = fixed_rate_expiry.DetectorConfig.from_rules_dict(rules)
    coverage_cfg = collateral_coverage_drop.DetectorConfig.from_rules_dict(rules)
    facility_codes = product_codes("lending", "facility")
    mortgage_codes = product_codes("lending", "mortgage")

    @tool
    def check_facility_utilization(prty_id: str) -> dict:
        """Check whether this client's credit facility (loan or overdraft)
        is drawn above the configured utilization threshold."""
        fac = _party_agreements(source, prty_id, facility_codes, as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        rows = []
        for _, agr in fac.iterrows():
            bal = source.daily_balance(as_of - timedelta(days=7), as_of)
            bal = bal[bal["AGRMNT_ID"] == agr["AGRMNT_ID"]].copy()
            bal["PRTY_ID"] = prty_id
            bal["AGRMNT_ORIG_LIM"] = agr["AGRMNT_ORIG_LIM"]
            rows.append(facility_utilization_spike.detect(bal, util_cfg, "agent"))
        result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        if result.empty:
            return {"status": "not_detected"}
        row = result.sort_values("event_date").iloc[-1]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "utilization_pct": float(row["utilization_pct"]),
                "drawn_amount": float(row["drawn_amount"]), "orig_limit": float(row["orig_limit"]),
                "evidence_ref": facility_utilization_spike.to_signal(row).evidence_ref}

    @tool
    def check_facility_maturity(prty_id: str) -> dict:
        """Check whether any of this client's credit facilities are
        approaching their close/maturity date."""
        fac = _party_agreements(source, prty_id, facility_codes, as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        result = facility_maturity_approaching.detect(fac, maturity_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "close_date": row["close_date"], "days_to_close": int(row["days_to_close"]),
                "orig_limit": (float(row["orig_limit"])
                               if row["orig_limit"] is not None and pd.notna(row["orig_limit"]) else None),
                "evidence_ref": facility_maturity_approaching.to_signal(row).evidence_ref}

    @tool
    def check_fixed_rate_expiry(prty_id: str) -> dict:
        """Check whether this client's mortgage fixed-rate period is
        approaching expiry."""
        mort_agreements = _party_agreements(source, prty_id, mortgage_codes, as_of)
        if mort_agreements.empty:
            return {"status": "no_mortgage"}
        mortgages, _ = source.read_entity("MORTGAGE_AGREEMENT", allow_unbounded=True)
        mortgages = mortgages[mortgages["AGRMNT_ID"].isin(mort_agreements["AGRMNT_ID"])].copy()
        mortgages["PRTY_ID"] = prty_id
        result = fixed_rate_expiry.detect(mortgages, fixed_rate_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"], "event_date": row["event_date"],
                "fixed_rate_end_date": row["fixed_rate_end_date"],
                "days_to_expiry": int(row["days_to_expiry"]),
                "evidence_ref": fixed_rate_expiry.to_signal(row).evidence_ref}

    @tool
    def check_collateral_coverage(prty_id: str) -> dict:
        """Check whether collateral coverage on this client's credit
        facilities has dropped below the required threshold, having
        previously been adequate."""
        fac = _party_agreements(source, prty_id, facility_codes, as_of)
        if fac.empty:
            return {"status": "no_credit_facility"}
        agr_coll, _ = source.read_entity("AGREEMENT_COLLATERAL_ITEM", allow_unbounded=True)
        links = agr_coll[agr_coll["AGRMNT_ID"].isin(fac["AGRMNT_ID"])]
        if links.empty:
            return {"status": "no_collateral"}
        # collateral_coverage_drop needs the FULL history (not just as-at
        # current) to tell "dropped" from "always thin" -- read the raw
        # table directly rather than the as_at-collapsed view.
        all_values, _ = source.read_entity(
            "COLLATERAL_ITEM_VALUE", allow_unbounded=True,
            columns=["CLTRL_ITEM_ID", "EFFECTIVE_START_DT", "CLTRL_VAL_AMT"],
        )
        merged = links.merge(all_values, on="CLTRL_ITEM_ID").merge(
            fac[["AGRMNT_ID", "AGRMNT_ORIG_LIM"]], on="AGRMNT_ID")
        merged["PRTY_ID"] = prty_id
        result = collateral_coverage_drop.detect(merged, coverage_cfg, "agent", as_of)
        if result.empty:
            return {"status": "not_detected"}
        row = result.iloc[0]
        return {"status": "detected", "agrmnt_id": row["agrmnt_id"],
                "cltrl_item_id": row["cltrl_item_id"], "event_date": row["event_date"],
                "current_coverage_pct": float(row["current_coverage_pct"]),
                "prior_max_coverage_pct": float(row["prior_max_coverage_pct"]),
                "evidence_ref": collateral_coverage_drop.to_signal(row).evidence_ref}

    return [check_facility_utilization, check_facility_maturity,
            check_fixed_rate_expiry, check_collateral_coverage]


def make_exogenous_tools(source: FdmLocalSource, rules: dict, event, as_of: date) -> list:
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
        qualified, magnitude = qualifies(prty_id, event, source, rules, as_of)
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


def make_risk_tools(source: FdmLocalSource, rules: dict, as_of: date) -> list:
    """A4 (docs/agentic_plan.md): the first domain wired through the
    registry entirely after M7, wiring the two detectors written ahead of
    Risk having a real registration (docs/current_state.md's "loose end"
    note). Only rating_downgrade is wired -- it reads PARTY, which the
    generator already populates with real bi-temporal RSK_GRD_CD/VAL
    history (config/entities_fdm.yaml). pd_migration needs PARTY_METRIC,
    which docs/entities_fdm.yaml's own header documents as deferred (no
    DDL captured) -- wiring it would mean inventing a table,
    docs/adding_a_new_domain.md's explicit "don't do this" case. It stays
    unregistered until that data exists."""
    rating_downgrade_cfg = rating_downgrade.DetectorConfig.from_rules_dict(rules)

    @tool
    def check_rating_downgrade(prty_id: str) -> dict:
        """Check whether this client's credit risk grade has worsened by
        at least the configured number of notches within the lookback
        window, versus the grade valid at the start of that window."""
        # PARTY is bi-temporal (Type 2 SCD) -- detect() needs every
        # version to compare "now" against "window_days ago", not the
        # as-at-collapsed single row party() returns. Same pattern
        # check_collateral_coverage already uses for COLLATERAL_ITEM_VALUE.
        all_versions, _ = source.read_entity("PARTY", allow_unbounded=True)
        versions = all_versions[all_versions["PRTY_ID"] == prty_id]
        if versions.empty:
            return {"status": "no_party_record"}
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
