"""
Whole-book demonstration for the FDM-aligned build: every synthetic
client runs through the SAME agentic pipeline AgentCore would invoke
(agents/orchestrator.py, batch mode -- deterministic tools, no LLM calls),
and the result lands as the RM worklist a relationship manager triages
from.

This is the VOLUME demo (all clients, seconds, no model). Its companion
agents/demo_multiagent_scenario.py is the DEPTH demo (one client, live LLM
narration per domain). Run both for a stakeholder session.

Deliberately prints the negative case and a book-wide governance check
alongside the positive case -- per docs/decision_record.md, "the negative
case is the real test", and a demo that only shows the happy path proves
nothing.

Run: python -m agents.demo_fdm_scenario
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import timedelta

import pandas as pd

from agents.orchestrator import evaluate_book
from datainsights.correlation.dedupe import dedupe
from datainsights.category_registry import revenue_categories
from datainsights.fdm_worklist import build_rm_worklist, write_rm_digest, write_rm_worklist_csv
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sinks.mimo_placeholder import write_insights
from external_events.exposure_qualifier import load_events
from external_events.extracted_event_store import read_extracted_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A1 (docs/agentic_plan.md): if an agent-extracted event exists, prefer it
# over the profile's configured event source -- same ExogenousEvent shape
# (external_events/extracted_event_store.py), so nothing downstream
# changes. Run `python -m external_events.ingest_notices` to populate
# this file from notice text; falls back to the profile's event source
# if it doesn't exist yet, so this demo still runs standalone.
EXTRACTED_EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm_extracted",
                                      "extracted_events.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fdm_local",
                        help="config/profiles/<name>.yaml -- see docs/generalization_plan.md Phase 0")
    args = parser.parse_args()

    print("=" * 78)
    print("DataInsights -- FDM whole-book run (volume demo)")
    print("Synthetic data only. Nothing here is a real client or a real event.")
    print("=" * 78)

    rt = build_runtime(args.profile)
    rules, source, OUT_DIR = rt.rules, rt.source, rt.out_dir
    data_dir = rt.profile.source.data_dir
    if data_dir and not os.path.isdir(data_dir if os.path.isabs(data_dir) else os.path.join(REPO_ROOT, data_dir)):
        raise SystemExit(
            f"Profile {args.profile!r} points at {data_dir!r}, which does not exist -- generate or "
            f"fetch that source's data first (see docs/user_guide.md)."
        )
    # R3 (docs/refactor_plan.md): every read below goes through the canonical
    # layer. This script used to call FdmLocalSource-only methods
    # (source.party(), .party_demographic(), .party_locator()) and so ran
    # against the fdm profile alone -- confirmed by running it on
    # legacy_local and watching it fail. Now any bound schema works.
    canonical = CanonicalSource(source, load_binding(rt.binding_name or "fdm"))

    extracted = read_extracted_events(EXTRACTED_EVENTS_PATH)
    if extracted:
        event = extracted[0]
        event_source = f"A1 extraction ({EXTRACTED_EVENTS_PATH})"
    else:
        event = load_events(rt.event_source_path)[0]
        event_source = f"profile event source ({rt.event_source_path})"
    as_of = event.event_date + timedelta(days=90)

    parties = canonical.read("Party", as_at=as_of)
    prty_ids = sorted(parties["party_id"])
    print(f"\n[1/4] {len(prty_ids)} clients as of {as_of}; external event in scope: "
          f"{event.event_type} ({event.affected_sector}/{event.affected_country}, "
          f"EUR {event.estimated_value_eur:,.0f})")
    print(f"      event source: {event_source}")

    print("[2/4] Running the agentic pipeline for every client (batch mode, no LLM)...")
    evaluations = evaluate_book(prty_ids, source=source, rules=rules, as_of=as_of, event=event,
                                binding_name=rt.binding_name or "fdm")
    n_signals = sum(len(e.signals) for e in evaluations)
    recommendations = dedupe([e.recommendation for e in evaluations if e.recommendation])
    print(f"      {n_signals} signals -> {len(recommendations)} recommendations after de-duplication")

    print("[3/4] Building the RM worklist...")
    worklist = build_rm_worklist(recommendations, source, rules, as_of, binding_name=rt.binding_name or "fdm")
    by_category = Counter(worklist["nba_category"])
    total_revenue = worklist["indicative_revenue_eur"].dropna().sum()
    print(f"      by category: {dict(by_category)}")
    print(f"      indicative revenue across the list: ~EUR {total_revenue:,.0f} (illustrative)")

    print("\nTop of the RM worklist:")
    for _, row in worklist.head(5).iterrows():
        revenue = (f"EUR {row['indicative_revenue_eur']:>9,.0f}"
                   if pd.notna(row["indicative_revenue_eur"]) else "      no revenue")
        print(f"  #{row['rank']:<2} {row['prty_id']}  {row['nba_category']:<22} {revenue}  "
              f"strength {row['signal_strength']}/5")

    print("\n" + "=" * 78)
    print("PROOF -- positive vs. negative case for the external event")
    print("=" * 78)
    # A binding without sector/country (legacy) yields an aligned all-empty
    # series, so the proof below honestly reports zero same-scope clients
    # instead of crashing on a misaligned indexer.
    sector = parties["sector_code"] if "sector_code" in parties.columns else pd.Series("", index=parties.index)
    country = parties["country_code"] if "country_code" in parties.columns else pd.Series("", index=parties.index)
    same_scope_ids = set(parties[(sector == event.affected_sector) & (country == event.affected_country)]["party_id"])
    in_scope = [e for e in evaluations if e.prty_id in same_scope_ids]
    confirmed = [e for e in in_scope if e.exogenous_confirmed]
    unconfirmed = [e for e in in_scope if not e.exogenous_confirmed]
    print(f"\n{len(in_scope)} clients share the event's sector AND country "
          f"(a naive sector+country match would contact all of them).")
    print(f"Exposure qualification confirmed {len(confirmed)}; rejected {len(unconfirmed)}.")
    for e in confirmed[:1]:
        rec = e.recommendation
        print(f"\n  POSITIVE -- {e.prty_id}: {rec.nba_category}, confirmed by "
              f"{', '.join(rec.confirming_domains)}")
        print(f"    {rec.recommended_action}")
    for e in unconfirmed[:1]:
        print(f"\n  NEGATIVE -- {e.prty_id}: same sector and country, no genuine exposure "
              f"in its own data -> not contacted about this event.")

    print("\n" + "=" * 78)
    print("GOVERNANCE -- HIGH_RSK_CUST_IND suppression, checked across the whole book")
    print("=" * 78)
    flagged = (set(parties.loc[parties["high_risk_flag"].fillna(False).astype(bool), "party_id"])
               if "high_risk_flag" in parties.columns else set())
    flagged_recs = [r for r in recommendations if r.prty_id in flagged]
    violations = [r for r in flagged_recs if r.nba_category in revenue_categories()]
    print(f"\n  {len(flagged)} high-risk-flagged clients; {len(flagged_recs)} have a "
          f"recommendation; {len(violations)} carry a revenue category "
          f"({'PASS' if not violations else 'FAIL -- investigate'}).")

    print("\n[4/4] Writing outputs...")
    csv_path = write_rm_worklist_csv(worklist, os.path.join(OUT_DIR, "fdm_rm_worklist.csv"))
    md_path = write_rm_digest(worklist, os.path.join(OUT_DIR, "fdm_rm_digest.md"), as_of,
                               run_notes=f"Whole-book FDM run: {len(prty_ids)} clients, "
                                         f"{n_signals} signals, event {event.event_type}.")
    json_path = write_insights(recommendations, os.path.join(OUT_DIR, "fdm_insights.json"), as_of=as_of)
    print(f"  RM worklist (CSV):     {csv_path}")
    print(f"  RM digest (markdown):  {md_path}")
    print(f"  MIMO-shaped JSON:      {json_path}  (schema-only, never sent anywhere)")


if __name__ == "__main__":
    main()
