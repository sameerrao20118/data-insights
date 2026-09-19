"""
Declarative event-type registry (docs/generalization_plan.md Phase 2,
R2) -- loads config/event_types.yaml and gives
external_events/exposure_qualifier.py and
datainsights/correlation/hypothesis.py typed, validated access to it.
Neither of those modules has a per-event-type branch any more; both
read a type's spec from here. Adding a type is a YAML edit (plus, if it
needs one, a new named check in external_events/exposure_checks.py) --
never a change to qualifies() or assemble().

Validated at LOAD time, not first use -- a registry with an unknown
check name, a missing correlation block, or an unresolvable match field
fails here with the offending type/field named, the same "catch a bad
mapping before any data is read" discipline
datainsights/semantic/validate.py applies to bindings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY_PATH = os.path.join(REPO_ROOT, "config", "event_types.yaml")

# Every ExogenousEvent attribute a match predicate's `equals`/`in` may
# reference directly (i.e. without a "payload." prefix).
_CORE_EVENT_FIELDS = {"event_type", "event_date", "affected_country", "affected_sector",
                     "severity", "estimated_value_eur"}

# Which of ExogenousEvent's fixed fields extraction.core_fields may name --
# event_date/event_type are handled unconditionally by
# external_events/event_extraction_agent.py, never listed here.
_EXTRACTABLE_CORE_FIELDS = {"affected_country", "affected_sector", "severity", "estimated_value_eur"}


class EventRegistryError(Exception):
    pass


@dataclass(frozen=True)
class EventTypeSpec:
    name: str
    description: str
    source: dict
    payload: dict            # field_name -> {type, required, default, pattern, enum, grounded_in_quote, currency, ...}
    match: list[dict]
    exposure: dict           # {"all_of": [...], "magnitude": {...}}
    correlation: dict | None  # None means this type never confirms a Recommendation
    # None means this type is not offered to event_extraction_agent.py at
    # all (config/event_types.yaml's own docstring covers the shape):
    # {"core_fields": {field_name: {pattern, enum, min, max, min_exclusive,
    # grounded_in_quote, currency, optional}}}.
    extraction: dict | None = None


def _validate_type(name: str, raw: dict, known_checks: set[str]) -> EventTypeSpec:
    problems = []

    for required_key in ("description", "match", "exposure"):
        if required_key not in raw:
            problems.append(f"event_types.{name}: missing required key {required_key!r}")

    payload = raw.get("payload", {}) or {}
    match = raw.get("match", []) or []
    exposure = raw.get("exposure", {}) or {}
    correlation = raw.get("correlation")

    for i, predicate in enumerate(match):
        f = predicate.get("field")
        target = predicate.get("equals") or predicate.get("in")
        if not f:
            problems.append(f"event_types.{name}.match[{i}]: missing 'field'")
        if not target:
            problems.append(f"event_types.{name}.match[{i}]: needs 'equals' or 'in'")
        elif isinstance(target, str) and not target.startswith("payload.") and target not in _CORE_EVENT_FIELDS:
            problems.append(f"event_types.{name}.match[{i}]: target {target!r} is not a core "
                            f"ExogenousEvent field or 'payload.<key>' -- known core fields: "
                            f"{sorted(_CORE_EVENT_FIELDS)}")

    checks = exposure.get("all_of", [])
    if not checks:
        problems.append(f"event_types.{name}.exposure: 'all_of' must list at least one check")
    for i, c in enumerate(checks):
        check_name = c.get("check")
        if check_name not in known_checks:
            problems.append(f"event_types.{name}.exposure.all_of[{i}]: unknown check "
                            f"{check_name!r} -- registered checks: {sorted(known_checks)}")

    magnitude = exposure.get("magnitude")
    if magnitude is None:
        problems.append(f"event_types.{name}.exposure: missing 'magnitude'")
    elif "from_check" in magnitude:
        referenced = magnitude["from_check"]
        if referenced not in {c.get("check") for c in checks}:
            problems.append(f"event_types.{name}.exposure.magnitude: from_check {referenced!r} "
                            f"is not one of this type's own exposure.all_of checks")
    elif "from_payload" not in magnitude:
        problems.append(f"event_types.{name}.exposure.magnitude: needs 'from_check' or 'from_payload'")

    if correlation is not None:
        if "hypothesis" not in correlation:
            problems.append(f"event_types.{name}.correlation: missing 'hypothesis'")
        sizing = correlation.get("sizing")
        if sizing is not None and "basis" not in sizing:
            problems.append(f"event_types.{name}.correlation.sizing: missing 'basis'")

    extraction = raw.get("extraction")
    if extraction is not None:
        core_fields = extraction.get("core_fields")
        if not core_fields:
            problems.append(f"event_types.{name}.extraction: missing or empty 'core_fields' "
                            f"(a type with nothing to extract shouldn't declare 'extraction:' at all)")
        else:
            for field_name in core_fields:
                if field_name not in _EXTRACTABLE_CORE_FIELDS:
                    problems.append(f"event_types.{name}.extraction.core_fields: {field_name!r} is not "
                                    f"an extractable core field -- one of {sorted(_EXTRACTABLE_CORE_FIELDS)}")
        for field_name, field_cfg in payload.items():
            if (field_cfg or {}).get("grounded_in_quote") and field_cfg.get("type") not in ("float", "int"):
                problems.append(f"event_types.{name}.payload.{field_name}: grounded_in_quote requires "
                                f"a numeric type (float/int), got {field_cfg.get('type')!r}")

    if problems:
        raise EventRegistryError("; ".join(problems))

    return EventTypeSpec(name=name, description=raw.get("description", ""), source=raw.get("source", {}),
                         payload=payload, match=match, exposure=exposure, correlation=correlation,
                         extraction=extraction)


@lru_cache(maxsize=8)
def _load(path: str) -> dict[str, EventTypeSpec]:
    from external_events.exposure_checks import CHECK_LIBRARY

    with open(path) as f:
        raw = yaml.safe_load(f)
    types = raw.get("event_types", {}) if raw else {}
    if not types:
        raise EventRegistryError(f"{path}: no event_types defined")
    return {name: _validate_type(name, spec, set(CHECK_LIBRARY)) for name, spec in types.items()}


def load_registry(path: str = DEFAULT_REGISTRY_PATH) -> dict[str, EventTypeSpec]:
    """All registered types, keyed by name. Cached per path -- re-reading
    the YAML on every qualifies() call would be wasteful; a test that
    loads a deliberately-broken registry file must call
    clear_registry_cache() first if it reuses a path already cached in
    the same process."""
    return _load(path)


def clear_registry_cache() -> None:
    _load.cache_clear()


def known_event_types(path: str = DEFAULT_REGISTRY_PATH) -> set[str]:
    return set(load_registry(path))


def spec(event_type: str, path: str = DEFAULT_REGISTRY_PATH) -> EventTypeSpec:
    registry = load_registry(path)
    if event_type not in registry:
        raise EventRegistryError(
            f"No exposure rule registered for event_type={event_type!r}. "
            f"Registered types: {sorted(registry)} -- see config/event_types.yaml."
        )
    return registry[event_type]
