"""
Verification for external_events/event_registry.py (docs/
generalization_plan.md Phase 2, R2) -- the registry is validated at
LOAD time, not first use, so a bad config/event_types.yaml edit fails
loudly with the offending type/field named rather than surfacing as a
confusing runtime KeyError deep inside qualifies().
"""

from __future__ import annotations

import os

import pytest
import yaml

from external_events.event_registry import (
    DEFAULT_REGISTRY_PATH,
    EventRegistryError,
    clear_registry_cache,
    known_event_types,
    load_registry,
    spec,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_registry(tmp_path, event_types: dict) -> str:
    path = os.path.join(tmp_path, "event_types.yaml")
    with open(path, "w") as f:
        yaml.safe_dump({"contract_version": 1, "event_types": event_types}, f)
    return path


# --- the real registry loads clean -----------------------------------------

def test_default_registry_loads_and_validates():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assert "public_tender_award" in registry
    assert "fx_rate_move" in registry


def test_known_event_types_includes_both_shipped_types():
    assert {"public_tender_award", "fx_rate_move"} <= known_event_types(DEFAULT_REGISTRY_PATH)


def test_spec_returns_typed_access():
    s = spec("public_tender_award", DEFAULT_REGISTRY_PATH)
    assert s.name == "public_tender_award"
    assert s.correlation is not None
    assert s.correlation["sizing"]["basis"] == "pct_of_event_value"


def test_unregistered_type_raises_with_message_naming_it():
    with pytest.raises(EventRegistryError, match="natural_disaster"):
        spec("natural_disaster", DEFAULT_REGISTRY_PATH)


# --- a deliberately broken registry fails at load time, not first use -----

VALID_TYPE = {
    "description": "test type",
    "match": [{"field": "sector_code", "equals": "affected_sector", "wildcard_if_empty": True}],
    "exposure": {
        "all_of": [{"check": "has_account", "product_class": "deposit"}],
        "magnitude": {"from_check": "has_account", "field": "account_count", "clamp": [0, 1]},
    },
    "correlation": {"hypothesis": "test hypothesis", "sizing": {"basis": "not_sized_this_pass", "label": "test"}},
}


@pytest.fixture(autouse=True)
def _clear_cache_around_each_test():
    clear_registry_cache()
    yield
    clear_registry_cache()


def test_unknown_check_name_fails_at_load(tmp_path):
    broken = dict(VALID_TYPE)
    broken["exposure"] = {
        "all_of": [{"check": "not_a_real_check"}],
        "magnitude": {"from_check": "not_a_real_check", "field": "x"},
    }
    path = _write_registry(tmp_path, {"broken_type": broken})
    with pytest.raises(EventRegistryError, match="unknown check.*not_a_real_check"):
        load_registry(path)


def test_missing_correlation_block_is_allowed_but_missing_hypothesis_is_not(tmp_path):
    """A type that never confirms a Recommendation may omit `correlation`
    entirely (e.g. informational-only events) -- but a `correlation`
    block that IS present must carry a hypothesis, or assemble() would
    silently fall back to no override, masking a config typo."""
    ok = dict(VALID_TYPE)
    ok.pop("correlation")
    path = _write_registry(tmp_path, {"no_correlation_type": ok})
    registry = load_registry(path)
    assert registry["no_correlation_type"].correlation is None

    bad = dict(VALID_TYPE)
    bad["correlation"] = {"sizing": {"basis": "not_sized_this_pass", "label": "x"}}  # no hypothesis
    path2 = _write_registry(tmp_path, {"bad_type": bad})
    clear_registry_cache()
    with pytest.raises(EventRegistryError, match="missing 'hypothesis'"):
        load_registry(path2)


def test_bad_magnitude_source_fails_at_load(tmp_path):
    bad = dict(VALID_TYPE)
    bad["exposure"] = {
        "all_of": [{"check": "has_account", "product_class": "deposit"}],
        "magnitude": {"from_check": "a_check_not_in_all_of", "field": "x"},
    }
    path = _write_registry(tmp_path, {"bad_type": bad})
    with pytest.raises(EventRegistryError, match="from_check"):
        load_registry(path)


def test_match_predicate_missing_field_fails_at_load(tmp_path):
    bad = dict(VALID_TYPE)
    bad["match"] = [{"equals": "affected_sector"}]  # no 'field'
    path = _write_registry(tmp_path, {"bad_type": bad})
    with pytest.raises(EventRegistryError, match="missing 'field'"):
        load_registry(path)


def test_empty_registry_fails_at_load(tmp_path):
    path = os.path.join(tmp_path, "empty.yaml")
    with open(path, "w") as f:
        yaml.safe_dump({"contract_version": 1, "event_types": {}}, f)
    with pytest.raises(EventRegistryError, match="no event_types"):
        load_registry(path)
