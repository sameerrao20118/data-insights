"""
Domain packs -- R4. A pack (config/packs/<name>.yaml) is the named,
coherent set of concepts + detectors + categories + default config a
deployment turns on. This loader validates a pack against the live
registries so a pack can never name something that does not exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS_DIR = os.path.join(REPO_ROOT, "config", "packs")


@dataclass(frozen=True)
class DomainPack:
    name: str
    description: str
    semantic_model_ref: str
    domains_ref: str
    categories_ref: str
    rules_ref: str
    event_types_ref: str | None
    concepts: tuple[str, ...]
    detectors: tuple[str, ...]
    categories: tuple[str, ...]


def pack_names() -> tuple[str, ...]:
    if not os.path.isdir(PACKS_DIR):
        return ()
    return tuple(sorted(f[:-5] for f in os.listdir(PACKS_DIR) if f.endswith(".yaml")))


@lru_cache(maxsize=8)
def load_pack(name: str = "banking") -> DomainPack:
    with open(os.path.join(PACKS_DIR, f"{name}.yaml")) as f:
        raw = yaml.safe_load(f) or {}
    return DomainPack(
        name=raw["name"], description=raw.get("description", ""),
        semantic_model_ref=raw["semantic_model_ref"], domains_ref=raw["domains_ref"],
        categories_ref=raw["categories_ref"], rules_ref=raw["rules_ref"],
        event_types_ref=raw.get("event_types_ref"),
        concepts=tuple(raw.get("concepts") or ()), detectors=tuple(raw.get("detectors") or ()),
        categories=tuple(raw.get("categories") or ()),
    )


def validate_pack(pack: DomainPack) -> list[str]:
    """Every name the pack uses must be declared by the registry it points at."""
    from datainsights import category_registry, domain_registry
    from datainsights.semantic.binding import load_semantic_model

    problems = []
    for ref in (pack.semantic_model_ref, pack.domains_ref, pack.categories_ref, pack.rules_ref, pack.event_types_ref):
        if ref and not os.path.exists(os.path.join(REPO_ROOT, ref)):
            problems.append(f"{pack.name}: ref {ref!r} does not exist")
    model = load_semantic_model(os.path.join(REPO_ROOT, pack.semantic_model_ref))
    for c in pack.concepts:
        if c not in model.concepts:
            problems.append(f"{pack.name}: concept {c!r} not in {pack.semantic_model_ref}")
    signals = domain_registry._all_signals(os.path.join(REPO_ROOT, pack.domains_ref))
    for d in pack.detectors:
        if d not in signals:
            problems.append(f"{pack.name}: detector {d!r} not a signal in {pack.domains_ref}")
    declared = set(category_registry.category_names(os.path.join(REPO_ROOT, pack.categories_ref)))
    for c in pack.categories:
        if c not in declared:
            problems.append(f"{pack.name}: category {c!r} not in {pack.categories_ref}")
    return problems
