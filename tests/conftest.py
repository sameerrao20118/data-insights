"""Shared test fixtures.

`navigate_to` is a fixture rather than an importable helper because
sibling test modules are not importable from each other, and pytest
injects conftest fixtures without any import at all.
"""

from __future__ import annotations

import pytest


def _navigate(at, page: str):
    """Select `page` in the dashboard sidebar, however it is offered.

    Deliberately not `at.sidebar.radio[0]`: that hardcoded both the widget
    type and the assumption of a single nav control, so regrouping the
    sidebar broke every page test for reasons unrelated to the pages.
    This finds whichever control actually holds the page, as a user would."""
    for widget in list(at.sidebar.selectbox) + list(at.sidebar.radio):
        if page in getattr(widget, "options", []):
            return widget.set_value(page).run()
    raise AssertionError(f"no sidebar control offers {page!r}")


@pytest.fixture
def navigate_to():
    return _navigate
