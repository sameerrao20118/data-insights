"""
R14 (docs/refactor_plan.md §6a): config that promises what the code does
not deliver. Five fields were declared in YAML/pydantic and read by
nothing -- a reviewer reading the config would believe the system
enforced concurrency limits, missing-data handling and challenger
promotion gates. It did not.

Rule: every declared field must be ENFORCED -- read by code outside its
own schema file, constrained by pydantic (a Literal or Field constraint
rejects a bad value before it ever loads, which is stronger than a
runtime read), or referenced inside a validator in its own schema file --
OR be listed in the register below with the task that will enforce it.
The register is checked in BOTH directions: a field that gains a reader
must be removed from it, so it can never silently grow.

Each field is checked against the ONE schema file that declares it.
Checking across all schema files at once confused `monitor.enabled`
(config.py, unread) with `MeasurePolicy.enabled` (policy.py, read).
"""

from __future__ import annotations

import os
import re
from typing import Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

from datainsights.config import Profile
from datainsights.ml.policy import SchemaPolicy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# field -> the task that will enforce it. Nothing gets in here without one.
DECLARED_NOT_YET_ENFORCED = {
    "defaults.require_challenger_win": "R7/E4 -- no promotion action exists yet to gate",
    "missing_data_policy": "R4 -- becomes a documentation field in the pack schema",
}

SEARCH_ROOTS = ["agents", "datainsights", "detection_engine", "external_events", "onboarding", "dashboard"]
PROFILE_SCHEMA = "datainsights/config.py"
ML_POLICY_SCHEMA = "datainsights/ml/policy.py"


def _strip_prose(text: str) -> str:
    """Docstrings and comments out -- a field name in prose is
    documentation, not a reader."""
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = re.sub(r"'''[\s\S]*?'''", "", text)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _code_corpus() -> dict[str, str]:
    corpus = {}
    for root_name in SEARCH_ROOTS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, root_name)):
            for f in files:
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(root, f), REPO_ROOT)
                    with open(os.path.join(root, f)) as fh:
                        corpus[rel] = _strip_prose(fh.read())
    return corpus


def _has_reader(field: str, corpus: dict[str, str], schema_file: str | None) -> bool:
    """Read outside the field's own schema file, or referenced inside that
    schema file beyond its declaration (i.e. in a validator body)."""
    parts = field.split(".")
    leaf = parts[-1]
    if len(parts) > 1:
        # nested: require the dotted attribute access (`monitor.enabled`) --
        # the bare leaf `enabled` matches half the codebase.
        outside = re.compile(r"\b" + re.escape(parts[-2]) + r"\." + re.escape(leaf) + r"\b")
    else:
        outside = re.compile(r"\b" + re.escape(leaf) + r"\b")
    if any(outside.search(text) for rel, text in corpus.items() if rel != schema_file):
        return True
    if schema_file is None:
        return False
    bare = re.compile(r"\b" + re.escape(leaf) + r"\b")
    declaration = re.compile(r"^\s*" + re.escape(leaf) + r"\s*:")
    non_decl = [line for line in corpus.get(schema_file, "").splitlines() if not declaration.match(line)]
    return any(bare.search(line) for line in non_decl)


def _schema_enforced(info) -> bool:
    """A Literal (closed value set) or a Field constraint is enforced by
    pydantic at load time -- an invalid value never gets in at all."""
    ann = info.annotation
    if get_origin(ann) is Literal or any(get_origin(a) is Literal for a in get_args(ann)):
        return True
    return bool(getattr(info, "metadata", None))


def _leaf_fields(model: type[BaseModel], prefix: str = "") -> list[tuple[str, bool]]:
    out = []
    for name, info in model.model_fields.items():
        ann = info.annotation
        sub = None
        for cand in getattr(ann, "__args__", ()) or (ann,):
            if isinstance(cand, type) and issubclass(cand, BaseModel):
                sub = cand
        if sub is not None:
            out += _leaf_fields(sub, f"{prefix}{name}.")
        else:
            out.append((f"{prefix}{name}", _schema_enforced(info)))
    return out


def _check(field: str, schema_enforced: bool, corpus: dict[str, str], schema_file: str | None, what: str):
    registered = field in DECLARED_NOT_YET_ENFORCED
    read = _has_reader(field, corpus, schema_file)
    if registered:
        assert not read, (f"{field!r} now HAS a reader -- remove it from DECLARED_NOT_YET_ENFORCED "
                          f"so the register keeps meaning something")
    else:
        assert read or schema_enforced, (
            f"{what} {field!r} is declared but enforced by nothing -- no reader, no Literal, no "
            f"constraint, no validator. Give it one, delete it, or register it with the task that will.")


@pytest.fixture(scope="module")
def corpus():
    return _code_corpus()


_ID = lambda v: v if isinstance(v, str) else ""  # noqa: E731


@pytest.mark.parametrize("field,schema_enforced", _leaf_fields(Profile), ids=_ID)
def test_every_profile_field_is_enforced_or_registered(field, schema_enforced, corpus):
    _check(field, schema_enforced, corpus, PROFILE_SCHEMA, "Profile field")


@pytest.mark.parametrize("field,schema_enforced", _leaf_fields(SchemaPolicy), ids=_ID)
def test_every_ml_policy_field_is_enforced_or_registered(field, schema_enforced, corpus):
    _check(field, schema_enforced, corpus, ML_POLICY_SCHEMA, "ML policy field")


ENTITY_CONTRACT_KEYS = ["physical_table", "domain", "grain", "primary_key", "required_columns",
                        "optional_columns", "time_semantics", "missing_data_policy"]


@pytest.mark.parametrize("key", ENTITY_CONTRACT_KEYS)
def test_every_entity_contract_key_is_read_or_registered(key, corpus):
    _check(key, False, corpus, None, "entity contract key")


def test_the_register_only_names_fields_that_exist():
    """A register entry for a field that was renamed or deleted would
    silently stop meaning anything."""
    declared = ({f for f, _ in _leaf_fields(Profile)} | {f for f, _ in _leaf_fields(SchemaPolicy)}
                | set(ENTITY_CONTRACT_KEYS))
    unknown = set(DECLARED_NOT_YET_ENFORCED) - declared
    assert not unknown, f"register names fields that no longer exist: {sorted(unknown)}"


def test_the_dead_field_this_task_found_is_gone():
    """product_codes: declared in domains_fdm.yaml, documented as read by
    step 5 of adding_a_new_domain.md, called by nothing. Deleted in R3."""
    with open(os.path.join(REPO_ROOT, "config", "domains_fdm.yaml")) as f:
        assert "product_codes" not in f.read()
    with open(os.path.join(REPO_ROOT, "datainsights", "domain_registry.py")) as f:
        assert "def product_codes" not in f.read()
