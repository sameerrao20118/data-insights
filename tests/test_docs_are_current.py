"""
Doc freshness, as a build guard.

Every doc in this repo claims things a reader will act on: a command to
run, a tab to click, a module to open. Those claims rot silently -- an
audit after Wave 4 found `architecture.md` still describing detectors
that "speak the FDM physical vocabulary" (false since R1), a dashboard
filter removed in R21, and rules.yaml blocks deleted in R23. This file
turns the mechanically checkable half of that into a test.

What it cannot check -- whether the prose still describes the design
honestly -- stays a human job.
"""

from __future__ import annotations

import importlib.util
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Docs a user or engineer is meant to follow. Historical records
# (changelog, plans, decision records) legitimately name deleted modules.
LIVE_DOCS = ["README.md", "docs/user_guide.md", "docs/architecture.md", "docs/ml_quickstart.md",
             "docs/adding_a_new_domain.md", "docs/model_change_procedure.md", "run_demo.sh"]
MODULE_RE = re.compile(r"python -m ([a-zA-Z_][a-zA-Z0-9_.]*)")
# `python -m X` invocations that are deliberately not importable here
ALLOWED_NON_MODULES = {"pytest", "ruff", "streamlit", "pip", "venv"}


def _text(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return f.read()


@pytest.mark.parametrize("rel", LIVE_DOCS)
def test_every_documented_module_exists(rel):
    """A `python -m ...` in a live doc must resolve. This is what catches
    a doc still telling a user to run something a refactor deleted."""
    missing = []
    for mod in sorted(set(MODULE_RE.findall(_text(rel)))):
        if mod in ALLOWED_NON_MODULES:
            continue
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, AttributeError, ValueError):
            found = False
        if not found:
            missing.append(mod)
    assert not missing, f"{rel} documents modules that do not exist: {missing}"


@pytest.mark.parametrize("rel", LIVE_DOCS)
def test_no_live_doc_points_at_a_deleted_module(rel):
    """R23 deleted a whole pipeline. A live doc naming one of its modules
    as something to use is a broken instruction, not history."""
    deleted = ["datainsights/cli.py", "datainsights/runner.py", "datainsights/ranking.py",
               "datainsights/worklist.py", "datainsights/build_worklist.py", "datainsights/digest.py",
               "datainsights/status.py", "datainsights/state.py",
               "detection_engine/external_macro_event.py", "external_events/demo_scenario.py",
               "external_events/simulate_external_events.py", "config/profiles/offline_ollama.yaml"]
    text = _text(rel)
    # A mention inside a paragraph that says it is gone is history, not an
    # instruction -- paragraph-scoped, because that disclaimer usually sits
    # a line or two away from the module name it applies to.
    retired = re.compile(r"retire|delet|gone|removed|R23|no longer", re.I)
    offenders = []
    for para in re.split(r"\n\s*\n", text):
        if retired.search(para):
            continue
        for path in deleted:
            if path in para or path.split("/")[-1].removesuffix(".py") in re.findall(r"python -m [\w.]+", para):
                offenders.append(f"{path}: {para.strip().splitlines()[0][:90]}")
    assert not offenders, f"{rel} still points at deleted modules:\n  " + "\n  ".join(offenders)


def test_user_guide_dashboard_table_lists_exactly_the_real_pages():
    from dashboard.tabs import PAGES

    text = _text("docs/user_guide.md")
    table = text[text.index("| Tab | Use it to |"):]
    table = table[:table.index("\n\n")]
    documented = {m.group(1) for m in re.finditer(r"^\| \*\*(.+?)\*\* \|", table, re.M)}
    assert documented == set(PAGES), (
        f"user_guide's dashboard table and dashboard/tabs/PAGES disagree: "
        f"only in doc {documented - set(PAGES)}, only in code {set(PAGES) - documented}")


def test_every_config_file_the_docs_promise_exists():
    """The "change behaviour" table is the one an engineer follows to
    configure this thing; a path in it must be real."""
    text = _text("docs/user_guide.md") + _text("docs/architecture.md")
    missing = sorted({p for p in re.findall(r"`(config/[A-Za-z0-9_/<>*.-]+\.yaml)`", text)
                      if "<" not in p and "*" not in p
                      and not os.path.exists(os.path.join(REPO_ROOT, p))})
    assert not missing, f"docs reference config files that do not exist: {missing}"
