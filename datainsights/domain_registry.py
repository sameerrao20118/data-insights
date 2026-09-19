"""
Domain registry (data half): allowed RM actions, and
per-endogenous-signal NBA category + hypothesis, loaded from
config/domains_fdm.yaml -- the file docs/adding_a_new_domain.md points a
new domain at. Read directly by agents/domain_agent.py (allowed_actions),
and datainsights/correlation/hypothesis.py (category_for/hypothesis_for), so
neither needs a rules dict
threaded through just for this.

Deliberately self-loading (module-level default path, resolved lazily)
rather than requiring a caller to pass a config dict: hypothesis.py's
assemble() must give real category/hypothesis text for "cash_buildup"
etc. even when the caller only did `from datainsights.correlation.hypothesis
import assemble` and never touched agents/* -- tests/test_correlation.py
does exactly that. See that module's own docstring.

The CODE half of the registry -- which Strands tool factory and which
detection_engine module each domain owns -- is agents/domain_registry.py,
a separate module, because it holds Python callables/modules (not data)
and only agents/orchestrator.py needs it. Splitting this way means
adding a domain edits config/domains_fdm.yaml (data) plus one register()
call in agents/tools.py (code) -- never orchestrator.py, domain_agent.py,
or hypothesis.py themselves. See docs/adding_a_new_domain.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

# Resolved relative to this file, not the process cwd -- existing modules
# in this repo (datainsights/runner.py, agents/demo_fdm_scenario.py) load
# config/*.yaml relative to cwd, which is fine for entry points but not
# for a module tests import directly from arbitrary invocation dirs.
_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "domains_fdm.yaml"
)


class UnknownDomainError(KeyError):
    pass


@lru_cache(maxsize=8)
def _load(path: str | None = None) -> dict:
    # `path` is read fresh from the module attribute on every call (not
    # baked into a function-signature default), specifically so tests can
    # monkeypatch datainsights.domain_registry._DEFAULT_PATH and call
    # _load.cache_clear() to point every accessor at a fixture file --
    # see tests/test_domain_registry.py's dummy-domain test.
    resolved = path or _DEFAULT_PATH
    with open(resolved) as f:
        return yaml.safe_load(f) or {}


def _domain(domain: str, path: str | None = None) -> dict:
    config = _load(path)
    if domain not in config:
        raise UnknownDomainError(domain)
    return config[domain]


# R10: top-level keys that are NOT domains. `combinations` is the
# cross-domain rules table (see the block at the end of domains_fdm.yaml).
_RESERVED_KEYS = frozenset({"combinations"})


def domain_names(path: str | None = None) -> tuple[str, ...]:
    return tuple(k for k in _load(path).keys() if k not in _RESERVED_KEYS)


@dataclass(frozen=True)
class CombinationRule:
    name: str
    when: tuple[str, ...]
    category: str
    hypothesis: str
    size_from: str | None = None


def combination_rules(path: str | None = None) -> list[CombinationRule]:
    """R10: the cross-domain rules, most specific first (longest `when`),
    YAML order breaking ties."""
    rules = []
    for r in _load(path).get("combinations") or []:
        missing = {"name", "when", "category", "hypothesis"} - set(r)
        if missing:
            raise ValueError(f"combination rule {r.get('name')!r} missing {sorted(missing)}")
        rules.append(CombinationRule(name=r["name"], when=tuple(r["when"]), category=r["category"],
                                     hypothesis=r["hypothesis"], size_from=r.get("size_from")))
    return sorted(rules, key=lambda r: -len(r.when))


def matching_combination(signal_types, path: str | None = None) -> CombinationRule | None:
    """The first rule whose every `when` signal is present, or None --
    a combination with no rule falls through to strongest-signal-wins."""
    present = set(signal_types)
    for rule in combination_rules(path):
        if set(rule.when) <= present:
            return rule
    return None


def allowed_actions(domain: str, path: str | None = None) -> tuple[str, ...]:
    return tuple(_domain(domain, path)["allowed_actions"])


def _all_signals(path: str | None = None) -> dict:
    """signal_type -> {category, hypothesis}, flattened across every
    registered domain. Signal types are the correlation key (a detector's
    signal_type is unique across the whole build today), so flattening
    here is safe -- a future collision would need domain-scoping, not
    hit yet."""
    flat: dict = {}
    for name, spec in _load(path).items():
        if name in _RESERVED_KEYS:
            continue
        flat.update(spec.get("signals") or {})
    return flat


def category_for(signal_type: str, default: str = "ADVISORY_ONLY", path: str | None = None) -> str:
    return _all_signals(path).get(signal_type, {}).get("category", default)


def hypothesis_for(signal_type: str, default: str = "Signal observed; reasoning not yet mapped.",
                    path: str | None = None) -> str:
    return _all_signals(path).get(signal_type, {}).get("hypothesis", default)


def why_now_for(signal_type: str, default: str = "", path: str | None = None) -> str:
    """The one-line 'why this client, this week' for the RM worklist (R2:
    moved here from a Python dict in datainsights/fdm_worklist.py)."""
    return _all_signals(path).get(signal_type, {}).get("why_now", default)


def non_revenue_action_for(signal_type: str,
                           default: str = "RM to review this signal -- no product offer.",
                           path: str | None = None) -> str:
    """Action text for a signal whose category never carries an offer (R2:
    moved here from datainsights/correlation/hypothesis.py)."""
    return _all_signals(path).get(signal_type, {}).get("non_revenue_action", default)


def is_ambiguous(signal_type: str, path: str | None = None) -> bool:
    """A2 (docs/agentic_plan.md): whether this signal_type's category
    mapping is a disclosed simplification worth an investigator agent's
    second look -- see config/domains_fdm.yaml's `ambiguous:` flag."""
    return bool(_all_signals(path).get(signal_type, {}).get("ambiguous", False))


def category_options(signal_type: str, path: str | None = None) -> list[str]:
    """The fixed set an investigator may propose between for this
    signal_type -- never free text, never a category outside this list.
    Empty for a non-ambiguous signal_type (nothing to investigate).
    config/domains_fdm.yaml's category_options is a mapping (category ->
    {requires_evidence: ...}); this returns just the category names."""
    opts = _all_signals(path).get(signal_type, {}).get("category_options", [])
    return list(opts.keys()) if isinstance(opts, dict) else list(opts)


def category_evidence_requirements(signal_type: str, path: str | None = None) -> dict[str, str]:
    """category -> the name of the evidence predicate
    (agents/investigator_agent.py's EVIDENCE_PREDICATES) that must hold
    in a client's own tool results before an investigator may propose
    that category for this signal_type. A category with no
    `requires_evidence` entry is unconstrained beyond being one of
    category_options() itself."""
    opts = _all_signals(path).get(signal_type, {}).get("category_options", {})
    if not isinstance(opts, dict):
        return {}
    return {cat: cfg["requires_evidence"] for cat, cfg in opts.items() if cfg and cfg.get("requires_evidence")}
