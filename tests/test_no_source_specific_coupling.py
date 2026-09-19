"""
The anti-coupling guard, widened (R3, docs/refactor_plan.md §6).

Before this rewrite it scanned FIVE files for FIVE table names -- so the
six canonical->FDM rename maps in agents/tools.py, every detector's FDM
REQUIRED_COLUMNS, and four modules calling FdmLocalSource-only methods
all passed it. A guard that cannot see the coupling it guards against
gives false confidence, which is worse than no guard.

Now: every pipeline package, every physical column name from every
entities_*.yaml contract, every FDM-only source method. The ONLY places
allowed to know physical vocabulary are the ones whose job it is:

  datainsights/sources/   -- reads physical tables; that IS its job
  data_generator/         -- writes physical tables; that IS its job
  onboarding/             -- profiles physical tables to propose bindings
  dashboard/              -- previews physical data by design (R22 splits
                             the pipeline-driving parts out and brings them
                             under this guard)
  tests/, docs/, config/  -- fixtures, prose, and the bindings themselves

Plus two named exceptions with a task attached, below.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUARDED_PACKAGES = ["agents", "detection_engine", "external_events", "datainsights/correlation",
                    "datainsights/ml", "datainsights/semantic"]
GUARDED_FILES = ["datainsights/fdm_worklist.py", "datainsights/runtime.py", "datainsights/features.py"]

# The LEGACY pipeline speaks the legacy physical vocabulary by design and
# is retired/ported in R23 -- excluded with that task attached, not silently.
EXCLUDED_WITH_REASON = {
    "datainsights/semantic/demo_canonical_read.py": "a demo that prints physical-vs-canonical side by side on purpose",
}

FDM_ONLY_SOURCE_METHODS = [".party(", ".agreement(", ".daily_balance(", ".financial_event(",
                           ".party_demographic(", ".party_locator(", ".collateral_item_value("]

# Names that are BOTH a canonical field and a physical column in some
# contract (the legacy contract's columns are lowercase). Using these is
# not coupling to a physical schema.
CANONICAL_LOOKALIKES = {"party_id", "account_id", "balance", "amount", "currency", "value",
                        "close_date", "posted_at", "observed_at", "direction", "transaction_id",
                        "segment", "sector", "country", "legal_name", "client_id", "collateral_id",
                        "original_limit", "valid_from", "valid_to", "grade_code", "grade_value",
                        "metric_type", "fixed_rate_end_date", "product_code", "product_class"}


def _detector_output_vocabulary() -> set[str]:
    """Every detector's DETECTION_COLUMNS -- the detector OUTPUT contract
    (status, orig_limit, event_date, ...) that sizing and the worklist
    consume. Some of these collide with a physical column in some contract
    (SBA's agreements.status / agreements.orig_limit). That is a name
    collision, not coupling: the code is reading its own output. Collected
    dynamically so a new detector's output fields are never mistaken for
    schema coupling either."""
    import importlib
    import pkgutil

    import detection_engine

    vocab: set[str] = set()
    for info in pkgutil.iter_modules(detection_engine.__path__):
        module = importlib.import_module(f"detection_engine.{info.name}")
        vocab.update(getattr(module, "DETECTION_COLUMNS", []))
    return vocab


def _physical_column_names() -> set[str]:
    names: set[str] = set()
    config = os.path.join(REPO_ROOT, "config")
    for fname in os.listdir(config):
        if fname.startswith("entities") and fname.endswith(".yaml"):
            with open(os.path.join(config, fname)) as f:
                contract = yaml.safe_load(f) or {}
            for spec in (contract.get("entities") or {}).values():
                for block in ("required_columns", "optional_columns"):
                    names.update((spec.get(block) or {}).keys())
    output_vocab = _detector_output_vocabulary()
    return {n for n in names if n not in CANONICAL_LOOKALIKES and n not in output_vocab}


def _guarded_python_files() -> list[str]:
    out = []
    for pkg in GUARDED_PACKAGES:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, pkg)):
            out += [os.path.relpath(os.path.join(root, f), REPO_ROOT) for f in files if f.endswith(".py")]
    return sorted(f for f in set(out + GUARDED_FILES) if f not in EXCLUDED_WITH_REASON)


def _code_lines(relpath: str) -> list[tuple[int, str]]:
    """Source lines with docstrings and comments stripped -- a physical
    name in prose is documentation; in code it is coupling."""
    with open(os.path.join(REPO_ROOT, relpath)) as f:
        text = f.read()
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = re.sub(r"'''[\s\S]*?'''", "", text)
    return [(i + 1, line.split("#", 1)[0]) for i, line in enumerate(text.splitlines())]


@pytest.mark.parametrize("relpath", _guarded_python_files())
def test_no_fdm_only_source_methods(relpath):
    hits = [(n, line.strip()) for n, line in _code_lines(relpath)
            for m in FDM_ONLY_SOURCE_METHODS if m in line]
    assert not hits, (f"{relpath} calls an FdmLocalSource-only method -- read through "
                      f"CanonicalSource instead: {hits[:3]}")


@pytest.mark.parametrize("relpath", _guarded_python_files())
def test_no_physical_column_names(relpath):
    physical = _physical_column_names()
    literal = re.compile(r"""["']([A-Za-z_]+)["']""")
    hits = [(n, name) for n, line in _code_lines(relpath) for name in literal.findall(line) if name in physical]
    assert not hits, (f"{relpath} hardcodes physical column name(s) {sorted({h[1] for h in hits})} "
                      f"at lines {[h[0] for h in hits][:5]} -- the canonical layer exists so that "
                      f"nothing above it needs to know these.")


# The ONE place that is supposed to construct concrete sources: the
# composition root (docs/generalization_plan.md Phase 0). It stays under
# the physical-column check above; only the construction check exempts it.
COMPOSITION_ROOTS = {"datainsights/runtime.py"}


def test_no_direct_fdm_local_source_construction():
    for relpath in _guarded_python_files():
        if relpath in COMPOSITION_ROOTS:
            continue
        assert not any("FdmLocalSource(" in line for _, line in _code_lines(relpath)), \
            f"{relpath} constructs FdmLocalSource directly"


def test_the_guard_itself_is_not_vacuous():
    """If the contract files ever stop yielding physical names, every test
    above passes trivially. Refuse that."""
    assert len(_physical_column_names()) > 20
