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
def test_every_page_renders_without_an_exception(page):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, f"{page!r} raised: {[e.value for e in at.exception]}"


def test_dispatcher_is_thin_and_pages_are_modules():
    with open(APP) as f:
        n = len(f.read().splitlines())
    assert n < 120, f"dashboard/app.py is {n} lines -- pages belong in dashboard/tabs/"
    tabs_dir = os.path.join(REPO_ROOT, "dashboard", "tabs")
    modules = {f[:-3] for f in os.listdir(tabs_dir) if f.endswith(".py") and f != "__init__.py"}
    assert len(modules) == len(_pages())
