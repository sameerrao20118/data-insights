"""
The multi-agent story on ONE real customer with live local LLM calls,
end to end through to the RM-facing output an RM actually works from.

Three Strands DomainAgents (Deposits, Lending, Exogenous) evaluate the
SAME client through agents/orchestrator.py -- the same pipeline AgentCore
would invoke and a batch job would loop -- and the deterministic
assembler combines their evidence into one Recommendation. That
recommendation is then written as an RM worklist CSV + readable digest
(datainsights/fdm_worklist.py), so the demo ends at what an RM reads, not
at a Python object.

Slower than demo_fdm_scenario.py on purpose (real Ollama calls, one per
agent). This is the depth demo; that one is the volume demo.

Run: python -m agents.demo_multiagent_scenario
"""

from __future__ import annotations

import argparse
import os
from datetime import timedelta

from agents.model_factory import get_model
from agents.orchestrator import evaluate_client
from datainsights.fdm_worklist import build_rm_worklist, write_rm_digest, write_rm_worklist_csv
from datainsights.runtime import build_runtime
from external_events.exposure_qualifier import load_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def print_agent(domain: str, result):
    print(f"\n--- {domain.capitalize()} agent ---")
    detected = {k: v for k, v in result.tool_evidence.items() if v.get("status") == "detected"}
    print(f"  Signals found:      {list(detected) or 'none'}")
    print(f"  Observed facts:     {result.observed_facts}")
    print(f"  Hypothesis:         {result.hypothesis}")
    print(f"  Suggested action:   {result.suggested_action}")
    print(f"  Narrative source:   {result.narrative_source}")


def main(customer_prty_id: str = "PRTY00036", profile: str = "fdm_local"):
    print("=" * 78)
    print("DataInsights -- multi-agent trace, one customer, live local LLM")
    print("Synthetic data only. Nothing here is a real client or a real event.")
    print("=" * 78)

    if not os.path.isdir(os.path.join(REPO_ROOT, "data_generator", "output_fdm")):
        raise SystemExit(
            "Generate the demo data first:\n"
            "  python -m data_generator.fdm.generate_fdm --seed 42\n"
            "  python -m data_generator.fdm.generate_fdm_events"
        )

    rt = build_runtime(profile)
    rules, source, OUT_DIR, TRACE_DB_PATH = rt.rules, rt.source, rt.out_dir, rt.state_db_path
    event = load_events(rt.event_source_path)[0]
    as_of = event.event_date + timedelta(days=90)
    model = get_model(rt.model_config)

    print(f"\nCustomer: {customer_prty_id}   As of: {as_of}   "
          f"External event: {event.event_type} ({event.affected_sector}/{event.affected_country})")
    print("\nSTEP 1 -- each domain agent independently evaluates the SAME customer")

    evaluation = evaluate_client(customer_prty_id, source=source, rules=rules, as_of=as_of,
                                  model=model, event=event, narrate=True, trace_db_path=TRACE_DB_PATH)
    for domain, result in evaluation.agent_results.items():
        print_agent(domain, result)

    print("\nSTEP 2 -- deterministic correlation (datainsights/correlation/, not an LLM)")
    rec = evaluation.recommendation
    if rec is None:
        print(f"  No recommendation for {customer_prty_id} this run.")
        return
    print(f"  Category:           {rec.nba_category}")
    print(f"  Confirmed by:       {', '.join(rec.confirming_domains)} (strength {rec.signal_strength}/5)")
    print(f"  Recommended action: {rec.recommended_action}")

    print("\nSTEP 3 -- RM-facing output")
    worklist = build_rm_worklist([rec], source, rules, as_of)
    row = worklist.iloc[0]
    revenue = (f"~EUR {row['indicative_revenue_eur']:,.0f} (illustrative)"
               if row["indicative_revenue_eur"] == row["indicative_revenue_eur"] else "none")
    print(f"  Client:             {row['segment']} / {row['sector']} / {row['country']}")
    print(f"  Why now:            {row['why_now']}")
    print(f"  Revenue to bank:    {revenue} -- {row['revenue_mechanism']}")
    print(f"  Suggested opening:  {row['talking_point']}")

    csv_path = write_rm_worklist_csv(worklist, os.path.join(OUT_DIR, "fdm_rm_worklist_demo.csv"))
    md_path = write_rm_digest(worklist, os.path.join(OUT_DIR, "fdm_rm_digest_demo.md"), as_of,
                               run_notes="Multi-agent demo: one customer, three domain agents.")
    print(f"\n  Worklist: {csv_path}\n  Digest:   {md_path}\n  Agent trace: {TRACE_DB_PATH}")
    print("\nThe agents narrated. The category, sizing, and revenue figure were decided")
    print("by deterministic code -- identical with or without the LLM (tests/test_orchestrator.py).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer", default="PRTY00036")
    parser.add_argument("--profile", default="fdm_local",
                        help="config/profiles/<name>.yaml -- see docs/generalization_plan.md Phase 0")
    args = parser.parse_args()
    main(args.customer, args.profile)
