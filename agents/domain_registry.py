"""
Domain registry (code half): which Strands tool factory a domain owns,
and which detection_engine module each of its tools' evidence maps
through for to_signal(). Registered once per domain by a `register()`
call at the bottom of agents/tools.py -- agents/orchestrator.py reads
this instead of a hardcoded per-domain dict, so adding a domain's tools
never requires editing orchestrator.py itself. See
docs/adding_a_new_domain.md.

This is agents-only (Python callables/modules, not data) -- the allowed
RM actions and per-signal category/hypothesis registry is
datainsights/domain_registry.py instead, a separate module, because
datainsights/correlation/hypothesis.py needs to read that one without
ever importing anything under agents/ (see its own docstring for why:
tests/test_correlation.py imports hypothesis.py directly, with no
agents/* import in the chain, and must still see real category/
hypothesis text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import Callable


@dataclass(frozen=True)
class DomainSpec:
    name: str
    # (source, rules, as_of) -> list[tool], matching make_deposits_tools/
    # make_lending_tools's signature. The one documented exception is
    # "exogenous", whose factory takes an extra `event` argument (it needs
    # a specific external event to check exposure against) --
    # agents/orchestrator.py calls that one explicitly rather than through
    # this uniform signature; see make_exogenous_tools's own docstring.
    make_tools: Callable[..., list]
    # tool_name -> detector module owning that tool's to_signal(). Empty
    # for a domain whose tools don't produce a correlation Signal (e.g.
    # exogenous -- its evidence feeds assemble()'s exogenous kwargs
    # instead, handled explicitly in orchestrator.py).
    detector_by_tool: dict[str, ModuleType] = field(default_factory=dict)


_REGISTRY: dict[str, DomainSpec] = {}


def register(spec: DomainSpec) -> None:
    _REGISTRY[spec.name] = spec


def get(name: str) -> DomainSpec:
    if name not in _REGISTRY:
        raise KeyError(f"domain not registered: {name}")
    return _REGISTRY[name]


def all_specs() -> dict[str, DomainSpec]:
    return dict(_REGISTRY)


def detector_by_tool() -> dict[str, ModuleType]:
    """Flattened tool_name -> detector module across every registered
    domain -- the direct replacement for agents/orchestrator.py's old
    module-level DETECTOR_BY_TOOL dict."""
    merged: dict[str, ModuleType] = {}
    for spec in _REGISTRY.values():
        merged.update(spec.detector_by_tool)
    return merged
