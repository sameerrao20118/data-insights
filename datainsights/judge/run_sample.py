"""
Runs the offline judge against a SAMPLE of cached narratives from the state
store -- not every narrative, not every monitoring event (project
instructions section 8: "Judge a sampled dataset offline to avoid running a
second model on every monitoring event"). Writes a small report and prints
disagreement/flag counts.

Known limitation, disclosed rather than hidden: narrator (qwen2.5:7b) and
judge (llama3.1:8b) are different models but both local Ollama models of
broadly similar scale and unknown training-data overlap -- this reduces but
does not eliminate correlated errors. Treat judge scores as a second,
imperfect opinion, not ground truth.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys

import yaml

from datainsights.judge.offline_judge import judge_one
from datainsights.narrative.evidence import ALLOWED_ACTIONS, EvidencePacket


def run_sample(state_path: str, base_url: str, judge_model: str, sample_n: int, seed: int = 7):
    con = sqlite3.connect(state_path)
    rows = con.execute(
        """SELECT n.detection_id, n.narrative_json, d.client_id, d.account_id, d.currency,
                  d.event_date, d.flagged_amount, d.rank, d.score
           FROM narratives n JOIN detections d ON n.detection_id = d.detection_id
           WHERE n.narrative_json LIKE '%ollama:%'"""
    ).fetchall()
    con.close()

    if not rows:
        print("No LLM-generated narratives found in state store yet -- run the pipeline "
              "with narratives enabled first.")
        return []

    random.Random(seed).shuffle(rows)
    sample = rows[:sample_n]

    results = []
    for (detection_id, narrative_json, client_id, account_id, currency, event_date,
         flagged_amount, rank, score) in sample:
        narrative = json.loads(narrative_json)
        evidence = EvidencePacket(
            detection_id=detection_id, rule_version="unknown", client_id=client_id,
            account_id=account_id, currency=currency, event_date=event_date,
            flagged_amount=flagged_amount, baseline_median=float("nan"), baseline_n=0,
            mad_multiples=float("nan"), rank=rank, score=score, allowed_actions=ALLOWED_ACTIONS,
        )
        result = judge_one(evidence, narrative, base_url=base_url, model=judge_model,
                            timeout_seconds=45)
        results.append(result)

    ok = [r for r in results if r["judge_status"] == "ok"]
    print(f"Judged {len(ok)}/{len(results)} narratives successfully "
          f"(judge model: {judge_model}, narrator: qwen2.5:7b -- different models, "
          f"see module docstring for the correlated-error caveat).")
    if ok:
        for dim in ("faithfulness", "action_relevance", "uncertainty_handling", "clarity"):
            vals = [r[dim] for r in ok]
            print(f"  {dim}: mean={sum(vals)/len(vals):.2f}, min={min(vals)}, max={max(vals)}")
        flagged = [r for r in ok if r.get("flagged_for_human_review")]
        print(f"  flagged_for_human_review: {len(flagged)}/{len(ok)}")
    return results


if __name__ == "__main__":
    with open("config/rules.yaml") as f:
        rules = yaml.safe_load(f)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    run_sample(
        state_path="var/state.sqlite",
        base_url="http://127.0.0.1:11434",
        judge_model=rules["narrative"]["judge_model"],
        sample_n=n,
    )
