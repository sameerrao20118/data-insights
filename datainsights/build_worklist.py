"""
CLI: regenerate the combined RM worklist CSV from whatever's already in the
state stores (both event categories). Doesn't re-run detection or call the
LLM -- run datainsights.cli and/or external_events.demo_scenario first.

Usage: python -m datainsights.build_worklist
"""

from datainsights.worklist import build_worklist, write_worklist_csv


def main():
    df = build_worklist(
        clients_csv_path="data_generator/output/clients.csv",
        transaction_state_path="var/state.sqlite",
        macro_state_path="var/state_external_events.sqlite",
        external_events_csv_path="external_events/output/external_events.csv",
    )
    out_path = write_worklist_csv(df, "var/insights")
    print(f"{len(df)} rows written to {out_path}")
    if not df.empty:
        print()
        print(df["category"].value_counts().to_string())
        print()
        print(df.head(10).to_string(index=False, max_colwidth=40))


if __name__ == "__main__":
    main()
