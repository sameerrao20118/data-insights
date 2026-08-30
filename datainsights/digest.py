"""
Local draft RM digest -- markdown file, no email/CRM delivery (project
instructions section 8: "no actual email delivery or CRM updates without
explicit authorization").
"""

from __future__ import annotations

from pathlib import Path


def write_digest(path: str, run_id: str, as_of: str, items: list[dict], run_notes: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    out_path = Path(path) / f"digest_{run_id}.md"

    lines = [
        f"# RM Digest -- run {run_id}",
        "",
        "**Synthetic proof-of-concept output. Not a real business "
        "recommendation, not derived from real client data.**",
        "",
        f"As of: {as_of}",
        "",
        run_notes,
        "",
        f"## {len(items)} active recommendation(s), ranked",
        "",
    ]
    if not items:
        lines.append("_No active detections this run._")
    for item in items:
        lines += [
            f"### #{item['rank']} — {item['client_id']} / {item['account_id']} "
            f"(score {item['score']:.2f})",
            "",
            f"- **Detection:** `{item['detection_id']}`",
            f"- **Event date:** {item['event_date']}",
            f"- **Flagged amount:** {item['flagged_amount']:,.2f} {item['currency']}",
            "",
            f"**Observed facts:** {item['narrative']['observed_facts']}",
            "",
            f"**Interpretation:** {item['narrative']['interpretation']}",
            "",
            f"**Suggested action:** {item['narrative']['suggested_action']}",
            "",
            f"**Caveats:** {item['narrative']['caveats']}",
            "",
            f"_Narrative source: {item['narrative']['narrative_source']}_",
            "",
            "---",
            "",
        ]

    out_path.write_text("\n".join(lines))
    return str(out_path)
