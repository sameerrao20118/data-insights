"""
Versioned prompt loader (docs/generalization_plan.md Phase 4) -- every
LLM-facing agent's system prompt template lives in prompts/<name>.yaml
at the repo root, not an inline f-string, so a change is a diffable,
versioned artifact instead of prose buried in Python.

`load_prompt(name)` returns `(template, version)`; a caller formats the
template with its own dynamic values (`domain`, `allowed_actions`, ...)
-- this loader doesn't know what placeholders any given template needs.

The governance mechanism this exists for is
tests/test_prompt_versioning.py's hash check: each template's content
hash is pinned per `version` in prompts/hashes.json. Editing a
template's text without bumping `version` in its own YAML file makes
that test fail, so a prompt change can't land silently -- the same
"can't quietly drift" discipline config/domains_fdm.yaml's
`requires_evidence` predicates and config/event_types.yaml's
`contract_version` already hold themselves to.

Scope, disclosed: covers every prompt that's a fixed template
(domain_agent, investigator_agent, rm_copilot_agent, and the
event-extraction agent's classify stage). The event-extraction agent's
EXTRACT-stage prompt is built dynamically per event type from
config/event_types.yaml's own schema (external_events/
event_extraction_agent.py's _build_extraction_prompt) -- versioning that
prompt's registered content is what event_types.yaml's own
`contract_version` already does; duplicating it here would be two
version numbers for one underlying source of truth, not real coverage.
"""

from __future__ import annotations

import os
from functools import lru_cache

import yaml

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


class PromptError(Exception):
    pass


@lru_cache(maxsize=16)
def load_prompt(name: str, path: str | None = None) -> tuple[str, int]:
    """(template, version) for prompts/<name>.yaml (or an explicit path,
    for tests pointed at a fixture file)."""
    resolved = path or os.path.join(PROMPTS_DIR, f"{name}.yaml")
    with open(resolved) as f:
        data = yaml.safe_load(f)
    if "template" not in data or "version" not in data:
        raise PromptError(f"{resolved}: missing 'template' or 'version' key")
    return data["template"], int(data["version"])


def clear_cache() -> None:
    load_prompt.cache_clear()
