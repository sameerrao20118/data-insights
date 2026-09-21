"""
Leakage boundary, enforced where it is actually at risk.

The dashboard's source data browser reads CSVs DIRECTLY with pandas to
build its table inventory and previews. That bypasses
OfflineLocalSource's own refusal of protected paths -- so the boundary
has to be re-enforced in the browser, and proven here.

`data_generator/output/protected_evaluator_only/trigger_events.csv` sits
INSIDE the legacy source's own directory, so a naive recursive walk
would list it and preview evaluator-only ground truth in the UI.

This test is deliberately NOT vacuous: it first asserts the protected
file really exists on disk, so it can never pass merely because the
fixture went missing.
"""

from __future__ import annotations

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DIR = os.path.join(REPO_ROOT, "data_generator", "output")
PROTECTED_CSV = os.path.join(LEGACY_DIR, "protected_evaluator_only", "trigger_events.csv")

pytestmark = pytest.mark.skipif(not os.path.isdir(LEGACY_DIR), reason="legacy data not generated")


def test_the_protected_file_exists_so_this_test_is_not_vacuous():
    assert os.path.exists(PROTECTED_CSV), (
        "the protected ground-truth file is missing -- this suite would pass "
        "trivially without actually testing the exclusion")


def test_source_browser_excludes_the_protected_directory(navigate_to):
    """The unit-level guarantee: _source_tables must never return a path
    under protected_evaluator_only, even though it walks recursively."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"))
    at.run(timeout=120)
    # Navigate via whichever control offers the page, not a fixed widget
    # index -- see tests/conftest.py for why.
    at = navigate_to(at, "Explore a source")
    at.selectbox(key="explore_source").set_value("Legacy schema").run(timeout=120)
    assert not at.exception, at.exception

    # Nothing rendered anywhere on the page may mention the protected table.
    rendered = []
    for frame in at.dataframe:
        rendered.append(frame.value.to_string())
    for element in list(at.markdown) + list(at.caption):
        rendered.append(str(element.value))
    blob = "\n".join(rendered)

    assert "trigger_events" not in blob, "the dashboard listed evaluator-only ground truth"
    assert "protected_evaluator_only" not in blob, "the dashboard exposed the protected path"
