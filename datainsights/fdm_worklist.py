"""
The RM-facing output of the FDM pipeline: one scannable worklist row per
(client, recommendation), plus a readable digest.

Why this exists separately from datainsights/worklist.py: that one builds
from the LEGACY pipeline's SQLite detection/narrative tables and legacy
client columns. This one builds from correlation Recommendation objects
and FDM entities. Same six categories, same "category + hypothesis +
sized action" shape (docs/PROJECT_CONTEXT.md section 6) -- deliberately
not a second vocabulary, just a second source.

Design driver: a relationship manager must be able to read a row and know
(1) who to call, (2) why now, (3) what to offer, (4) what it's worth to
the bank, and (5) what to actually say. A JSON insight record satisfies
none of those. Every revenue figure is an illustrative planning
assumption from config/rules.yaml's `fdm_revenue_model`, labelled as such
inline -- never presented as this bank's pricing or a commitment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import pandas as pd

from datainsights.correlation.hypothesis import Recommendation
from datainsights.semantic.binding import load_binding
from datainsights import category_registry, domain_registry
from datainsights.semantic.canonical import CanonicalSource

RM_WORKLIST_COLUMNS = [
    "rank", "prty_id", "relationship_manager_id", "segment", "sector", "country", "nba_category",
    "revenue_mechanism", "indicative_revenue_eur", "indicative_offer_eur", "currency",
    "why_now", "hypothesis", "recommended_action", "talking_point",
    "confirming_domains", "signal_strength", "endogenous_signal_type",
    "exogenous_event_type", "exogenous_event_date", "evidence_ref",
    "sizing_basis", "response_actions", "baseline_source", "recommendation_id", "ambiguous",
]

# Which revenue mechanism each category earns through. Wording matches
# datainsights/worklist.py's existing documented mapping so an RM sees the
# same explanation regardless of which pipeline produced the row.
# R2: the per-category revenue mechanism / talking point and the per-signal
# "why now" line moved to config -- config/categories.yaml and
# config/domains_*.yaml, read via datainsights/category_registry.py and
# datainsights/domain_registry.py. Never Python dicts again.


@dataclass(frozen=True)
class RevenueModel:
    """Illustrative planning assumptions from config/rules.yaml's
    fdm_revenue_model. Not this bank's pricing -- see that section's own
    disclosure line, which is carried into the output."""
    financing_nim_pct: float
    financing_arrangement_fee_pct: float
    financing_assumed_term_months: int
    treasury_spread_pct: float
    hedging_fee_pct: float

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "RevenueModel":
        d = rules["fdm_revenue_model"]
        return cls(
            financing_nim_pct=d["financing_nim_pct"],
            financing_arrangement_fee_pct=d["financing_arrangement_fee_pct"],
            financing_assumed_term_months=d["financing_assumed_term_months"],
            treasury_spread_pct=d["treasury_spread_pct"],
            hedging_fee_pct=d["hedging_fee_pct"],
        )


def indicative_revenue_eur(category: str, offer_amount_eur: float | None,
                            model: RevenueModel) -> float | None:
    """Indicative first-cycle revenue to the bank, or None where sizing a
    revenue number would be the wrong behaviour (RISK_REVIEW,
    ADVISORY_ONLY) or where no offer amount could be honestly grounded."""
    # R2: the formula is chosen by the category's `revenue_model` in
    # config/categories.yaml, not by a category-name branch here -- a new
    # revenue category earns revenue with a YAML edit, no Python.
    kind = category_registry.revenue_model(category)
    if kind == "none":
        return None
    if not offer_amount_eur or offer_amount_eur <= 0:
        return None
    if kind == "financing":
        term_fraction = model.financing_assumed_term_months / 12
        margin = offer_amount_eur * model.financing_nim_pct * term_fraction
        fee = offer_amount_eur * model.financing_arrangement_fee_pct
        return round(margin + fee, 2)
    if kind == "treasury":
        return round(offer_amount_eur * model.treasury_spread_pct, 2)
    if kind == "hedging":
        return round(offer_amount_eur * model.hedging_fee_pct, 2)
    return None


def _offer_amount_from_recommendation(rec: Recommendation) -> float | None:
    """Reads the structured amount the assembler already computed --
    never parses it back out of recommended_action's prose. None where
    the signal could not be honestly sized (see rec.sizing_basis)."""
    return rec.sized_offer_eur


def _client_context(canonical: CanonicalSource, as_of: date) -> pd.DataFrame:
    """Segment/sector/country per party -- who the RM is actually calling.
    Read through CanonicalSource, so this works unchanged against any
    bound schema (docs/generalization_plan.md Phase 1) -- a binding that
    doesn't provide sector/country (e.g. legacy, see
    config/bindings/legacy.yaml) simply leaves those columns absent;
    callers below degrade to an empty string, never a crash."""
    party = canonical.read("Party", as_at=as_of)
    if party.empty:
        return pd.DataFrame(columns=["party_id", "segment", "sector_name", "country_code",
                                     "relationship_manager_id"])
    for col in ("segment", "sector_name", "country_code", "relationship_manager_id"):
        if col not in party.columns:
            party[col] = ""
    return party[["party_id", "segment", "sector_name", "country_code", "relationship_manager_id"]]


def build_rm_worklist(recommendations: list[Recommendation], source, rules: dict, as_of: date,
                       binding_name: str = "fdm") -> pd.DataFrame:
    """One scannable row per recommendation, ranked by indicative revenue
    where one exists, then by signal strength -- so the RM's first rows
    are the ones most worth their next hour. Non-revenue categories
    (RISK_REVIEW, ADVISORY_ONLY) always sort last: they matter, but they
    are not what an RM opens the list to action. `source` is any
    DataSource matching a config/bindings/<binding_name>.yaml binding."""
    if not recommendations:
        return pd.DataFrame(columns=RM_WORKLIST_COLUMNS)

    model = RevenueModel.from_rules_dict(rules)
    canonical = CanonicalSource(source, load_binding(binding_name))
    context = _client_context(canonical, as_of).set_index("party_id")

    rows = []
    for rec in recommendations:
        offer = _offer_amount_from_recommendation(rec)
        revenue = indicative_revenue_eur(rec.nba_category, offer, model)
        ctx = context.loc[rec.prty_id] if rec.prty_id in context.index else None
        rows.append({
            "prty_id": rec.prty_id,
            "relationship_manager_id": ctx["relationship_manager_id"] if ctx is not None else "",
            "segment": ctx["segment"] if ctx is not None else "",
            "sector": ctx["sector_name"] if ctx is not None else "",
            "country": ctx["country_code"] if ctx is not None else "",
            "nba_category": rec.nba_category,
            "revenue_mechanism": category_registry.revenue_mechanism(rec.nba_category),
            "indicative_revenue_eur": revenue,
            "indicative_offer_eur": offer,
            "currency": rec.currency,
            "why_now": domain_registry.why_now_for(rec.endogenous_signal_type),
            "hypothesis": rec.hypothesis,
            "recommended_action": rec.recommended_action,
            "talking_point": category_registry.talking_point(rec.nba_category),
            "confirming_domains": ",".join(rec.confirming_domains),
            "signal_strength": rec.signal_strength,
            "endogenous_signal_type": rec.endogenous_signal_type,
            "exogenous_event_type": rec.exogenous_event_type or "",
            "exogenous_event_date": rec.exogenous_event_date or "",
            "evidence_ref": rec.evidence_ref,
            "sizing_basis": rec.sizing_basis,
            "response_actions": ",".join(rec.response_actions),
            "baseline_source": rec.baseline_source,
            "recommendation_id": rec.recommendation_id,
            "ambiguous": rec.ambiguous,
        })

    df = pd.DataFrame(rows)
    df["_revenue_sort"] = df["indicative_revenue_eur"].fillna(-1)
    df["_is_revenue_category"] = df["nba_category"].map(category_registry.is_revenue)
    df = df.sort_values(
        ["_is_revenue_category", "_revenue_sort", "signal_strength"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df.drop(columns=["_revenue_sort", "_is_revenue_category"])[RM_WORKLIST_COLUMNS]


def write_rm_worklist_csv(df: pd.DataFrame, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path


def write_rm_digest(df: pd.DataFrame, out_path: str, as_of: date, run_notes: str = "") -> str:
    """Readable prose version of the same rows -- for reading two or three
    clients closely, where the CSV is for triaging the whole book."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    revenue_rows = df[df["indicative_revenue_eur"].notna()]
    # R17: totals per currency -- never summed across currencies
    by_cur = revenue_rows.groupby("currency")["indicative_revenue_eur"].sum() if "currency" in df.columns \
        else {"EUR": revenue_rows["indicative_revenue_eur"].sum()}
    totals_text = ", ".join(f"~{c} {v:,.0f}" for c, v in dict(by_cur).items()) or "~EUR 0"

    lines = [
        "# RM worklist — FDM pipeline",
        "",
        "**Synthetic proof-of-concept output. Not real client data, not a real "
        "business recommendation, and not this bank's pricing.** Every monetary "
        "figure below is an illustrative planning assumption (see "
        "`config/rules.yaml`'s `fdm_revenue_model`), not a commitment or a quote.",
        "",
        f"As of: {as_of.isoformat()}",
        "",
        f"{len(df)} recommendation(s). "
        f"{len(revenue_rows)} carry an indicative revenue figure, totalling "
        f"{totals_text} (illustrative).",
        "",
    ]
    if run_notes:
        lines += [run_notes, ""]

    for _, row in df.iterrows():
        revenue = (f"~{row.get('currency', 'EUR')} {row['indicative_revenue_eur']:,.0f} (illustrative)"
                    if pd.notna(row["indicative_revenue_eur"]) else "none — not a revenue signal")
        lines += [
            f"## #{row['rank']} — {row['prty_id']} — {row['nba_category']}",
            "",
            f"- **Client:** {row['segment']} · {row['sector']} · {row['country']}",
            f"- **Why now:** {row['why_now']}",
            f"- **Indicative revenue to bank:** {revenue}",
            f"- **Revenue mechanism:** {row['revenue_mechanism']}",
            f"- **Confidence:** {row['signal_strength']}/5, confirmed by: {row['confirming_domains']}",
            "",
            f"**Hypothesis:** {row['hypothesis']}",
            "",
            f"**Recommended action:** {row['recommended_action']}",
            "",
            f"**Suggested opening:** {row['talking_point']}",
            "",
            f"_Evidence: `{row['evidence_ref']}` · sizing basis: `{row['sizing_basis']}` · "
            f"baseline: `{row['baseline_source']}`_",
            "",
            f"_RM response options: {row['response_actions']}_",
            "",
            "---",
            "",
        ]

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    return out_path
