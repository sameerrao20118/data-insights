"""
The RM worklist: one row per (client, active recommendation), across BOTH
event categories (endogenous transaction detections and exogenous macro
events) -- built from persisted state, not from a fresh detection run, so
it can be regenerated any time without re-calling the LLM.

This exists because a narrative digest (datainsights/digest.py) is good
for reading one recommendation closely, but useless for triaging a whole
book of clients -- an RM needs to scan, sort, and filter. This is that
view: client profile + category + recommended action + compact evidence,
one row each, CSV so it opens directly in a spreadsheet.

CATEGORY is the answer to "which kind of suggestion is this" -- it always
means the same six things regardless of which detector produced the row:

  FINANCING_NEED       -- likely needs a loan, guarantee, or credit line
  HEDGING_NEED         -- likely needs an FX/commodity hedge
  TREASURY_OPPORTUNITY -- likely has surplus cash for deposit/investment
  CAPEX_FINANCING      -- likely needs equipment/transition financing
  ADVISORY_ONLY        -- relationship conversation, no clear product yet
  RISK_REVIEW          -- credit/risk awareness, not a sales opportunity

These are honest best-guess tags from the event's shape, not a validated
product recommendation -- see each mapping's comment for the reasoning,
and revisit them if real conversion data ever shows a mapping is wrong.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

WORKLIST_COLUMNS = [
    "rank", "score", "category", "client_id", "legal_name", "segment", "sector",
    "country", "relationship_manager_id", "event_type", "hypothesis", "recommended_action",
    "evidence_summary", "event_date", "detection_id", "narrative_source",
]

# HYPOTHESIS is the "why" behind a category, one level up from the specific
# per-client action -- the general reasoning an RM can restate before the
# client-specific numbers. Same event_type+direction keying as
# categorize_macro_event(), and deliberately separate from it: this is
# prose for a human, that's a tag for a spreadsheet filter.
TRANSACTION_HYPOTHESIS = (
    "A payment landing well above this client's own historical pattern usually means a "
    "temporary cash surplus -- a short window where a treasury/deposit conversation is more "
    "relevant than usual, before the cash moves elsewhere."
)
MACRO_HYPOTHESIS = {
    ("rate_policy_change", "positive"):
        "A policy rate cut lowers borrowing costs economy-wide -- clients with existing or "
        "planned debt have a live reason to refinance or draw new financing now, before terms move.",
    ("rate_policy_change", "negative"):
        "A policy rate rise makes idle cash relatively more valuable to move -- clients have a "
        "live reason to place surplus cash into a higher-yielding deposit/investment product "
        "before conditions shift again.",
    ("public_tender_award", "positive"):
        "Winning a public tender creates a cash-flow gap between delivery and payment -- the "
        "winner needs working capital sized to the contract, not their balance sheet, and "
        "needs it before delivery starts, not after.",
    ("commodity_energy_shock", "negative"):
        "A sharp rise in energy prices raises input costs for energy-exposed sectors "
        "immediately, before it shows up in their margins -- a hedge locks in cost certainty "
        "while the exposure is fresh.",
    ("commodity_energy_shock", "positive"):
        "Falling energy prices ease cost pressure for energy-exposed sectors -- worth a "
        "check-in, but not a clear product need on its own.",
    ("natural_disaster", "negative"):
        "A regional disaster creates an immediate recovery/working-capital gap for affected "
        "clients before insurance or public relief arrives -- timing matters more than the "
        "exact loss figure.",
    ("eu_regulatory_change", "negative"):
        "A new regulation that raises compliance cost typically forces equipment or process "
        "changes on a deadline -- a capex financing need with a real timeline, not just interest.",
    ("eu_regulatory_change", "positive"):
        "An eased regulation removes a cost pressure -- worth flagging in a relationship "
        "conversation, not a financing trigger on its own.",
    ("sanctions_regulatory_change", "negative"):
        "A tightened sanctions/export-control change creates counterparty and compliance "
        "exposure for clients trading with the affected jurisdiction -- a risk-review trigger, "
        "not a sales opportunity, and it should stay that way.",
    ("sanctions_regulatory_change", "positive"):
        "An eased sanctions/export-control change can open a market that was previously "
        "restricted -- worth a risk-and-opportunity review together, not a standalone product pitch.",
    ("geopolitical_disruption", "negative"):
        "Regional instability disrupting trade routes raises delivery risk and cost for "
        "logistics-dependent clients -- flagged for risk awareness, not sized as a product "
        "opportunity, because the mechanism of harm is too indirect to price honestly.",
}


def macro_hypothesis(event_type: str, direction: str) -> str:
    return MACRO_HYPOTHESIS.get(
        (event_type, direction),
        "This event's sector/country overlaps the client's profile -- reviewed for relevance, "
        "no specific financing hypothesis applies to this event type/direction combination yet.",
    )

# large_incoming_payment always suggests the same commercial angle: a
# client's own cash just moved in a way that suggests a treasury/lending
# conversation, regardless of which specific transaction triggered it.
TRANSACTION_RULE_CATEGORY = {
    "large_incoming_payment.v1": "TREASURY_OPPORTUNITY",
}

# macro event_type -> category, direction-dependent where the same event
# type cuts both ways (a rate cut is a different conversation than a rate
# rise). Anything not resolvable this simply falls back to ADVISORY_ONLY
# rather than guessing.
def categorize_macro_event(event_type: str, direction: str) -> str:
    if event_type == "rate_policy_change":
        # a cut makes borrowing cheaper (financing conversation); a rise
        # makes deposits more attractive (treasury conversation)
        return "FINANCING_NEED" if direction == "positive" else "TREASURY_OPPORTUNITY"
    if event_type == "public_tender_award":
        return "FINANCING_NEED"  # winning a tender creates a working-capital need
    if event_type == "commodity_energy_shock":
        return "HEDGING_NEED" if direction == "negative" else "ADVISORY_ONLY"
    if event_type == "natural_disaster":
        return "FINANCING_NEED"  # recovery financing, not just sympathy
    if event_type == "eu_regulatory_change":
        return "CAPEX_FINANCING" if direction == "negative" else "ADVISORY_ONLY"
        # negative = compliance cost, often capex (equipment/process changes);
        # not reliably distinguishable further without more structure than
        # this simulated event carries -- an honest limitation, not hidden
    if event_type == "sanctions_regulatory_change":
        return "RISK_REVIEW"  # compliance/counterparty exposure review, not
                               # a sales moment -- see conversation note on
                               # the separate "displacement opportunity"
                               # mechanism this does NOT yet model
    if event_type == "geopolitical_disruption":
        return "RISK_REVIEW"
    return "ADVISORY_ONLY"


# Illustrative sizing heuristics -- NOT derived from real conversion data or
# real benchmark statistics. Each recommended_action below states its basis
# inline (event-level simulated value, or a named % of the client's own
# revenue) so an RM can see exactly what's a hard number vs. a planning
# rule-of-thumb, never presented as more certain than that. Revisit these
# the moment real accept/reject outcomes exist to calibrate against
# (see docs/gap_analysis.md).
REVENUE_PCT_HEURISTIC = {
    # (event_type, direction) -> (pct_of_revenue, purpose phrase)
    ("rate_policy_change", "positive"): (0.15, "a financing-lines review (cheaper borrowing)"),
    ("rate_policy_change", "negative"): (0.10, "a short-term deposit/investment placement (rates rising)"),
    ("commodity_energy_shock", "negative"): (0.12, "an FX/commodity hedge notional (energy cost exposure)"),
    ("eu_regulatory_change", "negative"): (0.08, "compliance-related capex financing"),
}
TENDER_ADVANCE_PCT = 0.25       # working-capital advance as a % of tender value
DISASTER_REVENUE_CAP_MULT = 1.5  # cap recovery financing at this multiple of revenue,
                                  # so a large regional damage estimate doesn't produce
                                  # an implausible ask against a small client's scale
TENDER_REVENUE_CAP_MULT = 2.0


def size_macro_action(event_type: str, direction: str, estimated_value_eur: float | None,
                       annual_revenue_eur_est: float | None) -> str | None:
    """A concrete, sized recommended action for a macro-event row, or None
    if this event type has no sizing rule (falls back to the narrative's
    own suggested_action, or the generic placeholder)."""
    has_value = estimated_value_eur is not None and pd.notna(estimated_value_eur)
    has_revenue = annual_revenue_eur_est is not None and pd.notna(annual_revenue_eur_est)

    if event_type == "public_tender_award" and has_value:
        amount = TENDER_ADVANCE_PCT * estimated_value_eur
        if has_revenue:
            amount = min(amount, TENDER_REVENUE_CAP_MULT * annual_revenue_eur_est)
        return (f"Offer working-capital financing of ~€{amount:,.0f} "
                f"({TENDER_ADVANCE_PCT:.0%} of the €{estimated_value_eur:,.0f} tender value, "
                f"illustrative) to bridge delivery before payment.")

    if event_type == "natural_disaster" and has_value:
        amount = estimated_value_eur
        if has_revenue:
            amount = min(amount, DISASTER_REVENUE_CAP_MULT * annual_revenue_eur_est)
        return (f"Offer recovery/working-capital financing of up to ~€{amount:,.0f} "
                f"(estimated regional damage €{estimated_value_eur:,.0f}, illustrative) "
                f"to help restore operations.")

    heuristic = REVENUE_PCT_HEURISTIC.get((event_type, direction))
    if heuristic and has_revenue:
        pct, purpose = heuristic
        amount = pct * annual_revenue_eur_est
        return f"Offer {purpose}: up to ~€{amount:,.0f} ({pct:.0%} of estimated annual revenue, illustrative)."

    return None


def _load_transaction_rows(state_path: str) -> list[dict]:
    con = sqlite3.connect(state_path)
    df = pd.read_sql_query(
        """SELECT d.*, n.narrative_json FROM detections d
           LEFT JOIN narratives n ON d.detection_id = n.detection_id
           WHERE d.status = 'detected'""",
        con,
    )
    con.close()
    rows = []
    for _, r in df.iterrows():
        narrative = json.loads(r["narrative_json"]) if pd.notna(r["narrative_json"]) else None
        rows.append({
            "rank": r["rank"], "score": r["score"], "client_id": r["client_id"],
            "category": TRANSACTION_RULE_CATEGORY.get(r["rule_version"], "ADVISORY_ONLY"),
            "event_type": r["rule_version"],
            "hypothesis": TRANSACTION_HYPOTHESIS,
            "recommended_action": narrative["suggested_action"] if narrative else
                f"Client received €{r['flagged_amount']:,.0f} on {r['event_date']} -- review "
                f"for a short-term deposit/investment placement (real transaction amount).",
            "evidence_summary": f"{r['flagged_amount']:,.2f} flagged on {r['event_date']} "
                                f"(detection {r['detection_id']})",
            "event_date": r["event_date"], "detection_id": r["detection_id"],
            "narrative_source": narrative["narrative_source"] if narrative else "",
        })
    return rows


def _load_macro_rows(state_path: str) -> list[dict]:
    con = sqlite3.connect(state_path)
    df = pd.read_sql_query(
        """SELECT d.*, n.narrative_json FROM detections d
           LEFT JOIN narratives n ON d.detection_id = n.detection_id
           WHERE d.status = 'detected'""",
        con,
    )
    con.close()
    rows = []
    for _, r in df.iterrows():
        narrative = json.loads(r["narrative_json"]) if pd.notna(r["narrative_json"]) else None
        # macro detections don't have direction/headline/severity persisted
        # in the shared `detections` table (see external_events/
        # demo_scenario.py's mapping into that table's transaction-shaped
        # columns) -- pull them back out of the detection_id-embedded
        # event_id by re-reading the source events file once, joined below
        # in build_worklist() instead of here, to keep this function only
        # responsible for what's actually in the state store.
        rows.append({
            "rank": r["rank"], "score": r["score"], "client_id": r["client_id"],
            "event_type": r["rule_version"], "recommended_action": narrative["suggested_action"] if narrative else "(no narrative generated)",
            "event_date": r["event_date"], "detection_id": r["detection_id"],
            "narrative_source": narrative["narrative_source"] if narrative else "",
            "transaction_id": r["transaction_id"],  # this is actually event_id, see the mapping note above
        })
    return rows


def build_worklist(clients_csv_path: str, transaction_state_path: str | None = None,
                    macro_state_path: str | None = None,
                    external_events_csv_path: str | None = None) -> pd.DataFrame:
    clients = pd.read_csv(clients_csv_path)[
        ["client_id", "legal_name", "segment", "sector", "country",
         "relationship_manager_id", "annual_revenue_eur_est"]
    ]

    all_rows = []
    if transaction_state_path:
        all_rows.extend(_load_transaction_rows(transaction_state_path))

    if macro_state_path and external_events_csv_path:
        events = pd.read_csv(external_events_csv_path).set_index("event_id")
        for row in _load_macro_rows(macro_state_path):
            event_id = row.pop("transaction_id")
            ev = events.loc[event_id]
            row["category"] = categorize_macro_event(ev["event_type"], ev["direction"])
            row["hypothesis"] = macro_hypothesis(ev["event_type"], ev["direction"])
            has_value = pd.notna(ev["estimated_value_eur"])
            value_clause = f", est. value €{ev['estimated_value_eur']:,.0f}" if has_value else ""
            row["evidence_summary"] = (
                f"{ev['event_type']} (severity {ev['severity']}/5, {ev['direction']}{value_clause}) on "
                f"{row['event_date']}: \"{ev['headline']}\" [{ev['real_source_type']} -- "
                f"{ev['source_context']}]"
            )
            # carried through to the sizing pass below (after the client
            # merge, since sizing needs annual_revenue_eur_est), then
            # dropped by the final WORKLIST_COLUMNS projection
            row["_macro_event_type"] = ev["event_type"]
            row["_direction"] = ev["direction"]
            row["_estimated_value_eur"] = ev["estimated_value_eur"] if has_value else None
            all_rows.append(row)

    if not all_rows:
        return pd.DataFrame(columns=WORKLIST_COLUMNS)

    df = pd.DataFrame(all_rows)
    df = df.merge(clients, on="client_id", how="left")

    if "_macro_event_type" in df.columns:
        def _sized_action(r):
            if pd.isna(r["_macro_event_type"]):
                return r["recommended_action"]
            sized = size_macro_action(
                r["_macro_event_type"], r["_direction"], r["_estimated_value_eur"],
                r.get("annual_revenue_eur_est"),
            )
            return sized or r["recommended_action"]
        df["recommended_action"] = df.apply(_sized_action, axis=1)

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1  # re-rank across the COMBINED worklist, not
                                 # each source's own internal rank
    return df[WORKLIST_COLUMNS]


def write_worklist_csv(df: pd.DataFrame, out_dir: str) -> str:
    from pathlib import Path
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(out_dir) / f"worklist_{ts}.csv"
    df.to_csv(out_path, index=False)
    return str(out_path)
