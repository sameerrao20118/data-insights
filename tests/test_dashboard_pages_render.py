"""
R22 (docs/refactor_plan.md §6k): the dashboard is one module per page
(dashboard/tabs/<page>.py) behind a thin dispatcher (dashboard/app.py),
and EVERY page renders under streamlit's AppTest in the suite -- before
this, the 1,976-line monolith was exercised only by ad-hoc scripts.
"""

from __future__ import annotations

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO_ROOT, "dashboard", "app.py")

def _pages() -> list[str]:
    from dashboard.tabs import PAGES

    return list(PAGES)


@pytest.mark.parametrize("page", _pages())
def test_every_page_renders_without_an_exception(page, navigate_to):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at = navigate_to(at, page)
    assert not at.exception, f"{page!r} raised: {[e.value for e in at.exception]}"


def test_every_page_has_exactly_one_sidebar_group():
    """PAGE_GROUPS is what the sidebar renders. A page missing from it
    would silently disappear from navigation while still existing in
    PAGES -- reachable by nothing."""
    from dashboard.tabs import PAGE_GROUPS, PAGES

    grouped = [name for _, names in PAGE_GROUPS for name in names]
    assert sorted(grouped) == sorted(PAGES), (
        "every page must appear in exactly one sidebar group: "
        f"ungrouped={sorted(set(PAGES) - set(grouped))}, "
        f"unknown={sorted(set(grouped) - set(PAGES))}")
    assert len(grouped) == len(set(grouped)), "a page appears in two groups"


def test_work_group_comes_first():
    """The entry point should not be a guess. Explore is the product; it
    leads the first group."""
    from dashboard.tabs import PAGE_GROUPS

    first_label, first_pages = PAGE_GROUPS[0]
    assert first_label == "Work"
    assert first_pages[0] == "Explore a source"


def test_dispatcher_is_thin_and_pages_are_modules():
    with open(APP) as f:
        n = len(f.read().splitlines())
    assert n < 120, f"dashboard/app.py is {n} lines -- pages belong in dashboard/tabs/"
    tabs_dir = os.path.join(REPO_ROOT, "dashboard", "tabs")
    modules = {f[:-3] for f in os.listdir(tabs_dir) if f.endswith(".py") and f != "__init__.py"}
    assert len(modules) == len(_pages())


def test_category_count_is_counted_not_hardcoded():
    """The heading read "The six recommendation categories" while the list
    under it was built from config/categories.yaml -- so a seventh category
    would have produced seven items under a heading saying six."""
    import re

    src = open(os.path.join(REPO_ROOT, "dashboard", "tabs", "technique.py")).read()
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    for word in ("six", "seven", "eight"):
        assert f"The {word} recommendation" not in src, (
            f"technique.py hardcodes a category count ({word}) -- count len(CATEGORY_INFO)")


def test_discovered_signals_are_surfaced_and_marked_shadow(navigate_to):
    """Three discovered signals existed with no way to see them from the
    dashboard, which made the whole discovery pipeline invisible. If any
    are accepted, the page must show them AND say they are held out."""
    from streamlit.testing.v1 import AppTest

    from onboarding.signal_accept import shadow_signal_types

    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at = navigate_to(at, "Technique reference")
    assert not at.exception, [e.value for e in at.exception]

    headings = " ".join(str(s.value) for s in at.subheader)
    assert "Signals" in headings, "live signals are not listed"

    if shadow_signal_types():
        assert "shadow" in headings.lower(), "discovered signals exist but are not surfaced"
        warned = " ".join(str(w.value) for w in at.warning).lower()
        assert "worklist" in warned, (
            "a shadow signal must be shown as held out of the pipeline, not as a normal signal")
