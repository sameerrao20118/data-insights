"""
Orchestrates: DataSource -> detector -> cooldown -> ranking -> narrative
(cached) -> state persistence -> digest. One run-once entry point, usable
from CLI or (later) a scheduler/service wrapper -- kept independent of any
particular invocation mechanism (project instructions section 4.1).
"""

from __future__ import annotations

import uuid
import yaml
from datetime import date

from datainsights import digest as digest_mod
from datainsights import ranking, state
from datainsights.config import Profile, load_profile
from datainsights.narrative import ollama_narrator
from datainsights.narrative.evidence import ALLOWED_ACTIONS, EvidencePacket
from datainsights.sources.offline_local import OfflineLocalSource
from detection_engine.large_incoming_payment import DetectorConfig, apply_cooldown, detect

DATASET_START = date(2023, 1, 1)


def _build_source(profile: Profile):
    if profile.source.backend == "offline_local":
        return OfflineLocalSource(profile.source.data_dir, profile.source.entity_map_ref)
    if profile.source.backend == "snowflake":
        from datainsights.sources.snowflake_source import SnowflakeSource
        # Fails closed here (EnvironmentError) if SNOWFLAKE_POC_* env vars
        # are unset -- see resolve_snowflake_connection. NOT RUN against a
        # real account by this session; see snowflake_source.py docstring.
        return SnowflakeSource(
            profile.source.connection_ref, profile.source.entity_map_ref,
            statement_row_limit=profile.source.statement_row_limit or 50_000,
            query_timeout_seconds=profile.source.query_timeout_seconds or 30,
        )
    raise NotImplementedError(f"source backend '{profile.source.backend}' is not supported")


def run_once(
    profile_name: str = "offline_ollama",
    as_of: date | None = None,
    generate_narratives: bool = True,
    max_narratives: int = 15,
) -> dict:
    profile = load_profile(profile_name)
    with open("config/rules.yaml") as f:
        rules = yaml.safe_load(f)
    detector_cfg = DetectorConfig.from_rules_dict(rules)
    ranking_cfg = ranking.RankingConfig.from_rules_dict(rules)
    as_of = as_of or DATASET_START  # caller must pass a real as_of for anything meaningful;
                                     # see cli.py which defaults sensibly per mode

    run_id = f"{profile_name}_{as_of.isoformat()}_{uuid.uuid4().hex[:8]}"
    source = _build_source(profile)

    with state.connect(profile.state.path) as con:
        state.start_run(con, run_id, mode="run_once", profile=profile_name)
        notes = []
        try:
            tx_df, provenance = source.read_entity(
                "transactions", start_date=DATASET_START, end_date=as_of
            )
            notes.append(
                f"Read {provenance.row_count:,} transaction rows from "
                f"{provenance.source_identity}, window {DATASET_START} to {as_of}. "
                f"NOTE: event-time replay -- booking_date is used as both event time "
                f"and detection-availability time (no separate ingestion timestamp "
                f"exists in this dataset)."
            )

            detections = detect(tx_df, detector_cfg, run_id=run_id)
            detections = apply_cooldown(detections, detector_cfg)
            ranked = ranking.rank(detections, as_of=as_of, config=ranking_cfg)

            n_new = state.upsert_detections(con, run_id, ranked)
            notes.append(
                f"{len(detections)} evaluated rows persisted "
                f"({(detections['status'] == 'detected').sum()} detected, "
                f"{(detections['status'] == 'suppressed_cooldown').sum()} suppressed by cooldown, "
                f"{(detections['status'] == 'insufficient_evidence').sum()} insufficient_evidence); "
                f"{n_new} newly seen this run (idempotent on rerun)."
            )

            digest_items = []
            narratives_generated = 0
            top = ranked.head(max_narratives) if generate_narratives else ranked.iloc[0:0]
            for _, row in top.iterrows():
                mad_mult = (
                    (row["flagged_amount"] - row["baseline_median"]) / row["baseline_mad"]
                    if row["baseline_mad"] else float("nan")
                )
                evidence = EvidencePacket(
                    detection_id=row["detection_id"], rule_version=row["rule_version"],
                    client_id=row["client_id"], account_id=row["account_id"],
                    currency=row["currency"], event_date=row["event_date"],
                    flagged_amount=row["flagged_amount"], baseline_median=row["baseline_median"],
                    baseline_n=row["baseline_n"], mad_multiples=mad_mult,
                    rank=int(row["rank"]), score=float(row["score"]),
                    allowed_actions=ALLOWED_ACTIONS,
                )
                cache_key = evidence.evidence_hash()
                narrative = state.get_cached_narrative(
                    con, cache_key, prompt_version="v1", model=rules["narrative"]["narrator_model"],
                    schema_version="v1",
                )
                if narrative is None:
                    narrative = ollama_narrator.render(
                        evidence, base_url=profile.llm.base_url, model=profile.llm.model,
                        timeout_seconds=rules["narrative"]["timeout_seconds"],
                        max_repair_attempts=rules["narrative"]["max_repair_attempts"],
                    )
                    state.cache_narrative(
                        con, cache_key, evidence.detection_id, evidence.rule_version,
                        prompt_version="v1", model=rules["narrative"]["narrator_model"],
                        schema_version="v1", narrative=narrative,
                    )
                    narratives_generated += 1
                digest_items.append({
                    **row.to_dict(), "narrative": narrative,
                    "subtitle": row["account_id"],
                    "detail_lines": [f"- **Flagged amount:** {row['flagged_amount']:,.2f} {row['currency']}"],
                })

            notes.append(
                f"{narratives_generated} narrative(s) newly generated this run "
                f"({len(digest_items) - narratives_generated} served from cache)."
            )

            digest_path = digest_mod.write_digest(
                profile.output.path, run_id, as_of.isoformat(), digest_items,
                run_notes="\n\n".join(notes),
            )

            state.finish_run(con, run_id, status="ok", notes="; ".join(notes))
            return {
                "run_id": run_id, "as_of": as_of.isoformat(), "status": "ok",
                "n_evaluated": len(detections), "n_active": int((ranked["status"] == "detected").sum())
                                                              if not ranked.empty else 0,
                "n_narratives_new": narratives_generated, "digest_path": digest_path,
                "notes": notes,
            }
        except Exception as e:
            state.finish_run(con, run_id, status="failed", notes=str(e))
            raise
