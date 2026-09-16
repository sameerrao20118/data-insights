"""
validate_binding(): checks a Binding against the physical contract it
claims to map (config/entities_fdm.yaml-shaped) BEFORE any data is read.
docs/generalization_plan.md Phase 1. Every check here is a config-time
guard -- a wrong binding should fail loudly at load time, not surface as
a KeyError three layers into a detector.
"""

from __future__ import annotations

import os

import yaml

from datainsights.semantic.binding import Binding


def validate_binding(binding: Binding, repo_root: str = ".") -> list[str]:
    """Returns a list of problems; empty means the binding is internally
    consistent with the contract it names. Does not touch actual data."""
    problems: list[str] = []
    contract_path = os.path.join(repo_root, binding.contract_ref)
    if not os.path.exists(contract_path):
        return [f"contract_ref {binding.contract_ref!r} does not exist"]
    with open(contract_path) as f:
        contract = yaml.safe_load(f)
    entities = contract.get("entities", {})

    for concept_name, cb in binding.concepts.items():
        if cb.unavailable:
            continue
        if cb.entity is None:
            problems.append(f"{concept_name}: no entity and no unavailable reason -- must have one or the other")
            continue
        if cb.entity not in entities:
            problems.append(f"{concept_name}: entity {cb.entity!r} not in contract {binding.contract_ref!r}")
            continue
        entity_cols = set(entities[cb.entity].get("required_columns", {}).keys())

        if cb.bitemporal:
            for label, col in (("valid_from", cb.bitemporal.valid_from), ("valid_to", cb.bitemporal.valid_to)):
                if col not in entity_cols:
                    problems.append(f"{concept_name}: bitemporal.{label}={col!r} not a column of {cb.entity}")

        for canon_field, phys_col in cb.fields.items():
            if phys_col not in entity_cols:
                problems.append(f"{concept_name}.{canon_field}: column {phys_col!r} not in {cb.entity}'s contract")

        for canon_field, spec in cb.derived.items():
            if spec.const is None and spec.from_ is None:
                problems.append(f"{concept_name}.{canon_field}: derived field needs 'const' or 'from'")
            if spec.from_ and spec.from_ not in entity_cols:
                problems.append(f"{concept_name}.{canon_field}: derived from {spec.from_!r}, "
                                f"not in {cb.entity}'s contract")

        # Joins can chain: a later join's `on` key may be a column that
        # only exists because an EARLIER join brought it in (e.g.
        # CollateralValuation: COLLATERAL_ITEM_VALUE -> (join on
        # CLTRL_ITEM_ID) AGREEMENT_COLLATERAL_ITEM -> (join on the
        # AGRMNT_ID that join just introduced) AGREEMENT). Track the
        # running set of available physical columns as joins are applied
        # in order, matching CanonicalSource._assemble()'s actual
        # left-to-right merge order.
        available_cols = set(entity_cols)
        for j in cb.joins:
            if j.entity not in entities:
                problems.append(f"{concept_name}: join entity {j.entity!r} not in contract")
                continue
            join_cols = set(entities[j.entity].get("required_columns", {}).keys())
            if j.on not in available_cols:
                problems.append(f"{concept_name}: join key {j.on!r} not available yet "
                                f"(not on {cb.entity} or introduced by an earlier join)")
            if j.on not in join_cols:
                problems.append(f"{concept_name}: join key {j.on!r} not a column of joined entity {j.entity}")
            for canon_field, phys_col in j.fields.items():
                if phys_col not in join_cols:
                    problems.append(f"{concept_name}.{canon_field}: joined column {phys_col!r} "
                                    f"not in {j.entity}'s contract")
            available_cols |= join_cols
    return problems
