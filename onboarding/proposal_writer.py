"""
Phase 5a schema onboarding -- renders binding_proposer.py's
ConceptProposal dict into a config/bindings/<name>.yaml-shaped YAML
proposal, plus a human-readable review report (confidence, evidence,
and every rejected/dropped mapping made visible, not hidden).

Scope, disclosed: proposes `entity` and plain `fields` renames only --
NOT `derived` (value maps, constants like currency) or `joins`
(multi-table concepts, e.g. fdm.yaml's Party pulling sector_code from a
second table via a join). Those need either a second, more complex LLM
proposal shape or human judgement about which physical columns actually
carry that semantic (a value map's exact code->value mapping isn't
something a profile's raw samples make unambiguous). A human refining
`derived`/`joins` into the proposed YAML before running
`onboarding/accept.py` is real, expected, disclosed usage -- not a
missing feature silently assumed away.
"""

from __future__ import annotations

import os

import yaml

from onboarding.binding_proposer import ConceptProposal

PROPOSALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proposals")


def proposal_to_binding_dict(schema_name: str, contract_ref: str, proposals: dict[str, ConceptProposal]) -> dict:
    concepts = {}
    for concept, p in proposals.items():
        if p.entity is None:
            concepts[concept] = {"unavailable": p.unavailable_reason or "no confident mapping proposed"}
        else:
            concepts[concept] = {"entity": p.entity, "fields": p.field_mappings}
    return {"schema": schema_name, "contract_ref": contract_ref, "concepts": concepts}


def binding_yaml_text(schema_name: str, contract_ref: str, proposals: dict[str, ConceptProposal]) -> str:
    header = (
        f"# PROPOSED by onboarding/binding_proposer.py for schema '{schema_name}' --\n"
        f"# NOT YET ACTIVE. Review this file (and onboarding/proposals/{schema_name}/review.md\n"
        f"# for confidence/evidence per mapping) before running:\n"
        f"#   python -m onboarding.accept {schema_name}\n"
        f"# `derived:`/`joins:` are NOT proposed -- add them by hand if a concept needs\n"
        f"# a value map, a constant, or a second-table join (see any file already in\n"
        f"# config/bindings/ for the pattern). Nothing here is registered until accepted.\n\n"
    )
    body = proposal_to_binding_dict(schema_name, contract_ref, proposals)
    return header + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def review_report(schema_name: str, proposals: dict[str, ConceptProposal]) -> str:
    lines = [f"# Onboarding review -- schema `{schema_name}`", ""]
    for concept, p in proposals.items():
        lines.append(f"## {concept}")
        if p.entity is None:
            lines.append(f"**UNAVAILABLE** -- {p.unavailable_reason}")
        else:
            lines.append(f"**Proposed entity**: `{p.entity}` (confidence {p.confidence:.2f})")
            lines.append(f"**Evidence**: {p.evidence}")
            lines.append("")
            lines.append("| canonical field | physical column |")
            lines.append("|---|---|")
            for canon, phys in p.field_mappings.items():
                lines.append(f"| {canon} | {phys} |")
            if p.rejected:
                lines.append("")
                lines.append("**Rejected (model proposed, but not a real column -- dropped):**")
                for r in p.rejected:
                    lines.append(f"- {r}")
        lines.append("")
    return "\n".join(lines)


def concepts_yaml_text(schema_name: str, concept_proposals) -> str:
    """R4: proposed NEW canonical concepts (onboarding/concept_proposer.py)
    in config/semantic_model.yaml's own shape, so accepting one is a
    paste into that file -- a deliberate human edit, never automated."""
    header = (f"# PROPOSED new canonical concepts for schema '{schema_name}' -- tables that mapped\n"
              f"# onto no existing concept but carry eligible measures. Review, then paste an\n"
              f"# accepted entry into config/semantic_model.yaml `concepts:` and bind it in\n"
              f"# config/bindings/{schema_name}.yaml. Rejected proposals are listed in review.md.\n\n")
    body = {}
    for c in concept_proposals:
        if c.accepted:
            body[c.name] = {"kind": c.kind, "key": c.key, "bitemporal": False,
                            "fields": {f: {"type": "string", "required": f == c.key} for f in c.fields},
                            "_from_table": c.table, "_business_meaning": c.business_meaning}
    return header + (yaml.safe_dump({"concepts": body}, sort_keys=False) if body else "concepts: {}\n")


def profile_yaml_text(schema_name: str, data_dir: str, contract_ref: str) -> str:
    """R8: the profile a run needs, proposed alongside the binding so
    accept -> run works with no hand edits. Shape copied from
    config/profiles/sba_local.yaml (a flat CSV directory); the llm block
    mirrors the currently active profile so the model id has one source."""
    from datainsights.runtime import active_profile

    active = active_profile()
    profile = {
        "config_version": 1, "profile": f"{schema_name}_local", "runtime": {"target": "local"},
        "source": {"backend": "offline_local_flat", "entity_map_ref": contract_ref, "data_dir": data_dir,
                   "binding": schema_name, "access": "read_only", "cost_policy": "no_cost_local_files"},
        "analytics": {"backend": "duckdb_local"},
        "llm": {"provider": "ollama", "base_url": active.llm.base_url or "http://127.0.0.1:11434",
                "model": active.llm.model, "allow_remote_inference": False, "allow_paid_fallback": False,
                "fallback": "deterministic_template"},
        "state": {"backend": "sqlite", "path": "var/agent_traces.db"},
        "output": {"backend": "local", "path": "var/insights"},
        "monitor": {"enabled": False, "interval_minutes": 60, "max_concurrent_runs": 1},
        "cost": {"paid_llm_calls_allowed": False, "paid_cloud_services_allowed": False},
    }
    header = (f"# PROPOSED profile for schema '{schema_name}' (onboarding/propose.py, R8). Becomes\n"
              f"# config/profiles/{schema_name}_local.yaml on `python -m onboarding.accept {schema_name}`.\n"
              f"# No event_source: add one if an exogenous event fixture exists for this schema.\n\n")
    return header + yaml.safe_dump(profile, sort_keys=False)


def concept_review_section(concept_proposals) -> str:
    if not concept_proposals:
        return ""
    lines = ["", "## Proposed NEW concepts (tables that mapped onto nothing)", ""]
    for c in concept_proposals:
        if c.accepted:
            lines.append(f"- `{c.table}` -> **{c.name}** ({c.kind}, key `{c.key}`, confidence {c.confidence:.2f}): "
                         f"{c.business_meaning} -- see concepts.proposed.yaml")
        else:
            lines.append(f"- `{c.table}` -> **NEEDS REVIEW** -- {c.rejected_reason}")
    return "\n".join(lines) + "\n"


def write_proposal(schema_name: str, contract_ref: str, proposals: dict[str, ConceptProposal],
                   contract_yaml: str, *, concept_proposals=(), profile_yaml: str | None = None) -> str:
    """Writes onboarding/proposals/<name>/{binding.proposed.yaml,
    entities.proposed.yaml, concepts.proposed.yaml, profile.proposed.yaml,
    review.md}. Returns the directory path. Entirely under onboarding/ --
    never touches config/ until onboarding/accept.py is run."""
    out_dir = os.path.join(PROPOSALS_DIR, schema_name)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "binding.proposed.yaml"), "w") as f:
        f.write(binding_yaml_text(schema_name, contract_ref, proposals))
    with open(os.path.join(out_dir, "entities.proposed.yaml"), "w") as f:
        f.write(contract_yaml)
    with open(os.path.join(out_dir, "concepts.proposed.yaml"), "w") as f:
        f.write(concepts_yaml_text(schema_name, list(concept_proposals)))
    if profile_yaml is not None:
        with open(os.path.join(out_dir, "profile.proposed.yaml"), "w") as f:
            f.write(profile_yaml)
    with open(os.path.join(out_dir, "review.md"), "w") as f:
        f.write(review_report(schema_name, proposals) + concept_review_section(list(concept_proposals)))
    return out_dir
