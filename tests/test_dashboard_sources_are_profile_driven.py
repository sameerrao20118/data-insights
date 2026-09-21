"""
The Explore page's three agentic tabs must run against the SELECTED
source's profile, not a hardcoded one.

Before this, `legacy` was tagged `proof_only` and the three tabs called
`build_runtime("fdm_local")` and read `output_fdm/kernel/party.csv`'s
PRTY_ID column directly. The visible symptom was the legacy panel
contradicting itself: the description said R23 had unified the pipeline
and named the command that produces its worklist, while the text
underneath said no whole-book generator was wired up. Verified by running
it: legacy produces 167 recommendations.

These tests guard the fix, because nothing else would catch it -- the
dashboard is deliberately outside tests/test_no_source_specific_coupling.py's
guarded packages, so a physical column name reappearing there is invisible
to that check.
"""

from __future__ import annotations

import re

import pytest

from dashboard.common import DATA_SOURCES, worklist_path_for

EXPLORE = "dashboard/tabs/explore.py"


def _source_code() -> str:
    with open(EXPLORE) as f:
        return f.read()


def _code_without_docstrings() -> str:
    """A physical name in prose is documentation; in code it is coupling --
    same distinction tests/test_no_source_specific_coupling.py draws."""
    text = _source_code()
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return re.sub(r"#.*", "", text)


def test_every_source_declares_a_profile():
    for key, src in DATA_SOURCES.items():
        assert src.get("profile"), f"source {key!r} declares no profile"


def test_full_agentic_sources_declare_a_picker_concept():
    """The client picker reads a canonical concept through the binding.
    Without this key it would have to fall back to a physical table."""
    for key, src in DATA_SOURCES.items():
        if src["kind"] == "full_agentic":
            assert src.get("picker_concept"), f"{key!r} is full_agentic but declares no picker_concept"


def test_legacy_is_wired_to_the_whole_book_path():
    """R23 unified the pipeline; the dashboard entry lagged behind it."""
    legacy = DATA_SOURCES["legacy"]
    assert legacy["kind"] == "full_agentic"
    assert legacy["profile"] == "legacy_local"


def test_no_hardcoded_profile_in_the_explore_page():
    code = _code_without_docstrings()
    assert "fdm_local" not in code, (
        "explore.py hardcodes the fdm_local profile -- the selected source's "
        "own profile must drive every run, or a non-FDM source silently "
        "shows FDM's data")


def test_no_physical_schema_names_in_the_explore_page():
    """PRTY_ID / party.csv are FDM's physical names. The picker must read
    canonical `party_id` through CanonicalSource instead."""
    code = _code_without_docstrings()
    for name in ("PRTY_ID", "party.csv"):
        assert name not in code, (
            f"explore.py references the physical name {name!r} -- read the "
            f"canonical concept through CanonicalSource instead")


def test_worklist_paths_are_distinct_per_profile():
    """agents/demo_fdm_scenario.py always writes fdm_rm_worklist.csv, and
    every local profile shares var/insights -- so without a per-profile
    snapshot, running legacy overwrites FDM's worklist and each tab shows
    the other source's book."""
    paths = {key: worklist_path_for(src["profile"]) for key, src in DATA_SOURCES.items()}
    assert len(set(paths.values())) == len(paths), f"worklist paths collide: {paths}"


@pytest.mark.parametrize("profile", sorted({s["profile"] for s in DATA_SOURCES.values()}))
def test_each_declared_profile_actually_loads(profile):
    """A profile named here but absent from config/profiles/ would only
    fail when a user clicked the tab."""
    from datainsights.runtime import active_profile

    assert active_profile(profile) is not None


@pytest.mark.parametrize("key", [k for k, s in DATA_SOURCES.items() if s["kind"] == "full_agentic"])
def test_client_picker_lists_clients_for_each_agentic_source(key):
    """The end-to-end check that matters: each agentic source can list its
    OWN clients through its OWN binding. FDM returns PRTY*, legacy returns
    CL* -- different id shapes, same code path."""
    from dashboard.tabs.explore import _client_ids

    src = DATA_SOURCES[key]
    try:
        ids = _client_ids(src["profile"], src["picker_concept"])
    except FileNotFoundError:
        pytest.skip(f"{key}: generated data not present")
    assert ids, f"{key}: no clients listed"
    assert all(isinstance(i, str) for i in ids)
