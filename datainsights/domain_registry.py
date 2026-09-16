"""
Domain registry (data half): product codes, allowed RM actions, and
per-endogenous-signal NBA category + hypothesis, loaded from
config/domains_fdm.yaml -- the file docs/adding_a_new_domain.md points a
new domain at. Read directly by agents/domain_agent.py (allowed_actions),
datainsights/correlation/hypothesis.py (category_for/hypothesis_for), and
agents/tools.py (product_codes), so none of the three needs a rules dict
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


def domain_names(path: str | None = None) -> tuple[str, ...]:
    return tuple(_load(path).keys())


def allowed_actions(domain: str, path: str | None = None) -> tuple[str, ...]:
    return tuple(_domain(domain, path)["allowed_actions"])


def product_codes(domain: str, group: str | None = None, path: str | None = None) -> list[str]:
    """Agreement type codes for this domain, e.g. deposits -> ["DEP"],
    lending's "facility" group -> ["LON", "ODR"]. `group` is required
    when a domain declares more than one group (lending has both
    "facility" and "mortgage" -- they're scoped to different tools, see
    agents/tools.py); omit it for a single-group or ungrouped domain."""
    codes = _domain(domain, path).get("product_codes") or {}
    if not codes:
        return []
    if group is not None:
        return list(codes.get(group, []))
    if len(codes) == 1:
        return list(next(iter(codes.values())))
    raise ValueError(f"domain {domain!r} has multiple product_codes groups {list(codes)} -- pass group=")


def _all_signals(path: str | None = None) -> dict:
    """signal_type -> {category, hypothesis}, flattened across every
    registered domain. Signal types are the correlation key (a detector's
    signal_type is unique across the whole build today), so flattening
    here is safe -- a future collision would need domain-scoping, not
    hit yet."""
    flat: dict = {}
    for spec in _load(path).values():
        flat.update(spec.get("signals") or {})
    return flat


def category_for(signal_type: str, default: str = "ADVISORY_ONLY", path: str | None = None) -> str:
    return _all_signals(path).get(signal_type, {}).get("category", default)


def hypothesis_for(signal_type: str, default: str = "Signal observed; reasoning not yet mapped.",
                    path: str | None = None) -> str:
    return _all_signals(path).get(signal_type, {}).get("hypothesis", default)


def is_ambiguous(signal_type: str, path: str | None = None) -> bool:
    """A2 (docs/agentic_plan.md): whether this signal_type's category
    mapping is a disclosed simplification worth an investigator agent's
    second look -- see config/domains_fdm.yaml's `ambiguous:` flag."""
    return bool(_all_signals(path).get(signal_type, {}).get("ambiguous", False))


def category_options(signal_type: str, path: str | None = None) -> list[str]:
    """The fixed set an investigator may propose between for this
    signal_type -- never free text, never a category outside this list.
    Empty for a non-ambiguous signal_type (nothing to investigate)."""
    return list(_all_signals(path).get(signal_type, {}).get("category_options", []))
