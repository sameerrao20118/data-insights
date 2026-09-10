"""
Runnable demonstration: exogenous events -> matched clients -> ranked ->
narrated -> RM digest. This is the "demonstrate how users would be
benefited" deliverable from docs/gap_analysis.md.

Run: python -m external_events.demo_scenario
"""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import yaml

from datainsights import digest as digest_mod
from datainsights import ranking, state
from datainsights.narrative import macro_narrator
from datainsights.narrative.macro_evidence import ALLOWED_ACTIONS_MACRO, MacroEvidencePacket
from datainsights.sources.external_event_source import SimulatedExternalEventSource
from detection_engine.external_macro_event import MacroDetectorConfig, apply_cooldown, detect

AS_OF = date(2025, 12, 31)


def main(max_narratives: int = 8, generate_narratives: bool = True):
    with open("config/rules.yaml") as f:
        rules = yaml.safe_load(f)
    macro_cfg = MacroDetectorConfig(
        min_severity=rules["external_macro_event"]["min_severity"],
        cooldown_days=rules["external_macro_event"]["cooldown_days"],
    )
    ranking_cfg = ranking.RankingConfig.from_rules_dict(rules, section="ranking_macro")

    print("Loading clients (real dataset -- data_generator/output/clients.csv)...")
    clients = pd.read_csv("data_generator/output/clients.csv")[["client_id", "sector", "country"]]

    print("Loading simulated external events...")
    source = SimulatedExternalEventSource("external_events/output/external_events.csv")
    events, provenance = source.read_events(end_date=AS_OF)
    print(f"  {provenance.row_count} events available "
          f"(real-source mapping: {source.event_type_catalog()})")

    run_id = f"external_macro_demo_{AS_OF.isoformat()}_{uuid.uuid4().hex[:8]}"

    print("Matching events to clients by sector/country...")
    detections = detect(events, clients, macro_cfg, run_id=run_id)
    detections = apply_cooldown(detections, macro_cfg)
    print(f"  {len(detections)} client-event matches "
          f"({(detections['status'] == 'detected').sum()} active, "
          f"{(detections['status'] == 'suppressed_cooldown').sum()} suppressed by cooldown)")

    ranked = ranking.rank_macro(detections, as_of=AS_OF, config=ranking_cfg)
    print(f"  ranked {len(ranked)} active recommendations")

    with state.connect("var/state_external_events.sqlite") as con:
        state.start_run(con, run_id, mode="demo_scenario", profile="offline_ollama")
        # state.upsert_detections was written against the transaction
        # detector's column shape (account_id, currency, transaction_id,
        # flagged_amount). Rather than widen that shared table for one
        # caller, map the closest equivalents here -- severity stands in
        # for flagged_amount so it's still visible/sortable in the state
        # store, and event_id stands in for transaction_id.
        state_ready = ranked.assign(
            account_id="", currency="", transaction_id=ranked["event_id"],
            flagged_amount=ranked["severity"].astype(float),
        )
        n_new = state.upsert_detections(con, run_id, state_ready)
        print(f"  {n_new} newly seen this run (idempotent on rerun)")

        digest_items = []
        narratives_generated = 0
        top = ranked.head(max_narratives) if generate_narratives else ranked.iloc[0:0]
        for _, row in top.iterrows():
            evidence = MacroEvidencePacket(
                detection_id=row["detection_id"], rule_version=row["rule_version"],
                client_id=row["client_id"], event_id=row["event_id"], event_date=row["event_date"],
                event_type=row["event_type"], source_name=row["source_name"],
                real_source_type=row["real_source_type"], source_context=row["source_context"],
                affected_country=row["affected_country"],
                affected_sector=row["affected_sector"], direction=row["direction"],
                severity=int(row["severity"]), headline=row["headline"], description=row["description"],
                rank=int(row["rank"]), score=float(row["score"]), allowed_actions=ALLOWED_ACTIONS_MACRO,
            )
            cache_key = evidence.evidence_hash()
            narrative = state.get_cached_narrative(
                con, cache_key, prompt_version="v1", model=rules["narrative"]["narrator_model"],
                schema_version="v1_macro",
            )
            if narrative is None:
                narrative = macro_narrator.render(
                    evidence, base_url="http://127.0.0.1:11434", model=rules["narrative"]["narrator_model"],
                    timeout_seconds=rules["narrative"]["timeout_seconds"],
                    max_repair_attempts=rules["narrative"]["max_repair_attempts"],
                )
                state.cache_narrative(
                    con, cache_key, evidence.detection_id, evidence.rule_version,
                    prompt_version="v1", model=rules["narrative"]["narrator_model"],
                    schema_version="v1_macro", narrative=narrative,
                )
                narratives_generated += 1
            digest_items.append({
                **row.to_dict(), "narrative": narrative,
                "subtitle": f"{row['event_type']} ({row['real_source_type']})",
                "detail_lines": [
                    f"- **Headline:** {row['headline']}",
                    f"- **Severity:** {row['severity']}/5 · **Direction:** {row['direction']}",
                    f"- **Scope:** sector={row['affected_sector'] or 'any'}, "
                    f"country={row['affected_country'] or 'EU-wide'}",
                ],
            })

        print(f"{narratives_generated} narrative(s) generated this run "
              f"({len(digest_items) - narratives_generated} served from cache)")

        digest_path = digest_mod.write_digest(
            "var/insights", run_id, AS_OF.isoformat(), digest_items,
            run_notes=(
                f"EXTERNAL MACRO EVENT DEMO. {provenance.row_count} simulated events matched "
                f"against {len(clients)} real clients by sector/country. All events are "
                f"synthetic -- see external_events/README.md for the real source each event "
                f"type would come from in production."
            ),
        )
        state.finish_run(con, run_id, status="ok",
                          notes=f"{len(digest_items)} digest items, {narratives_generated} new narratives")

    print(f"\nDigest written to {digest_path}")
    return digest_path


if __name__ == "__main__":
    main()
