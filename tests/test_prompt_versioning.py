"""
Phase 4 prompt versioning (docs/generalization_plan.md): every LLM-facing
agent's prompt lives in prompts/<name>.yaml (datainsights/prompts.py),
not an inline f-string, with a `version:` field. This is the mechanism
that stops a prompt edit from landing silently -- prompts/hashes.json
pins a content hash per (name, version); editing a template's text
without bumping `version` in its own YAML file makes this test fail.

To make a real prompt change: edit the template AND bump its `version`,
then run `python -m tests.test_prompt_versioning --update` (or just
delete the stale entry and re-run this file with UPDATE_HASHES=1) to
record the new pinned hash -- a deliberate act, not automatic.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from datainsights.prompts import load_prompt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HASHES_PATH = os.path.join(REPO_ROOT, "prompts", "hashes.json")

VERSIONED_PROMPTS = [
    "domain_agent",
    "investigator_agent",
    "rm_copilot_agent",
    "event_extraction_classify",
]


def _hash(template: str) -> str:
    return hashlib.sha256(template.encode()).hexdigest()[:16]


def _load_pinned_hashes() -> dict:
    if not os.path.exists(HASHES_PATH):
        return {}
    with open(HASHES_PATH) as f:
        return json.load(f)


@pytest.mark.parametrize("name", VERSIONED_PROMPTS)
def test_prompt_file_has_template_and_version(name):
    template, version = load_prompt(name)
    assert template.strip()
    assert version >= 1


@pytest.mark.parametrize("name", VERSIONED_PROMPTS)
def test_prompt_content_matches_its_pinned_hash(name):
    """The actual governance check: a template's text must match the
    hash pinned for its OWN declared version. If someone edits the YAML
    template's text without bumping `version`, the version number stays
    the same but the hash no longer matches -- this fails, on purpose."""
    template, version = load_prompt(name)
    pinned = _load_pinned_hashes()
    key = f"{name}@{version}"
    assert key in pinned, (
        f"{key!r} has no pinned hash in prompts/hashes.json -- a new version "
        f"was declared but never recorded. Run tests/test_prompt_versioning.py "
        f"with UPDATE_HASHES=1 to pin it deliberately."
    )
    actual = _hash(template)
    assert actual == pinned[key], (
        f"{name}.yaml's template text changed but its version ({version}) "
        f"didn't move -- bump `version:` in prompts/{name}.yaml, then update "
        f"prompts/hashes.json (UPDATE_HASHES=1) to pin the new text deliberately."
    )


def _update_hashes() -> None:
    pinned = _load_pinned_hashes()
    for name in VERSIONED_PROMPTS:
        template, version = load_prompt(name)
        pinned[f"{name}@{version}"] = _hash(template)
    with open(HASHES_PATH, "w") as f:
        json.dump(pinned, f, indent=2, sort_keys=True)
        f.write("\n")


if os.environ.get("UPDATE_HASHES") == "1":
    _update_hashes()
