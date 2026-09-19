"""
Category registry -- R2 (docs/refactor_plan.md). Loads config/categories.yaml
the same way datainsights/domain_registry.py loads config/domains_*.yaml:
self-loading, lru-cached, path-overridable for tests. Every place that
used to hardcode a category string set, label, colour, revenue mechanism,
talking point or revenue formula branch now asks here.
"""

from __future__ import annotations

import os
from functools import lru_cache

import yaml

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "categories.yaml"
)

VALID_REVENUE_MODELS = ("financing", "treasury", "hedging", "none")


class UnknownCategoryError(KeyError):
    pass


@lru_cache(maxsize=8)
def _load(path: str | None = None) -> dict:
    with open(path or _DEFAULT_PATH) as f:
        raw = yaml.safe_load(f) or {}
    for name, spec in (raw.get("categories") or {}).items():
        model = spec.get("revenue_model", "none")
        if model not in VALID_REVENUE_MODELS:
            raise ValueError(f"category {name!r}: revenue_model {model!r} must be one of {VALID_REVENUE_MODELS}")
    return raw


def _category(name: str, path: str | None = None) -> dict:
    cats = _load(path).get("categories") or {}
    if name not in cats:
        raise UnknownCategoryError(name)
    return cats[name]


def category_names(path: str | None = None) -> tuple[str, ...]:
    return tuple((_load(path).get("categories") or {}).keys())


def revenue_model(name: str, path: str | None = None) -> str:
    return _category(name, path).get("revenue_model", "none")


def is_revenue(name: str, path: str | None = None) -> bool:
    return revenue_model(name, path) != "none"


def revenue_categories(path: str | None = None) -> frozenset[str]:
    return frozenset(c for c in category_names(path) if is_revenue(c, path))


def label(name: str, path: str | None = None) -> str:
    return _category(name, path).get("label", name)


def description(name: str, path: str | None = None) -> str:
    return _category(name, path).get("description", "")


def colour(name: str, path: str | None = None) -> str:
    return _category(name, path).get("colour", "#666666")


def revenue_mechanism(name: str, path: str | None = None) -> str:
    return _category(name, path).get("revenue_mechanism", "")


def talking_point(name: str, path: str | None = None) -> str:
    return _category(name, path).get("talking_point", "")


def suppressed_action(path: str | None = None) -> str:
    return _load(path).get("suppressed_action", "RM to review before any product conversation.")
