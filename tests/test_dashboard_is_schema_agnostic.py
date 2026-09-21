"""
The dashboard must fetch data for WHATEVER source is selected -- not for a
fixed one, and not only for the schemas that happen to exist today.

Testing against the schemas in the repo proves those schemas work. It does
not prove the code is generic: a hardcoded `fdm_local` passes such a test
whenever FDM is one of the two being compared. So these tests create a
NEW source at runtime and assert the dashboard reads through it.

Two classes of bug this catches, both of which were real:
  * reading a fixed profile   -> another source's numbers under this label
  * reading a fixed file path -> FDM's event feed shown under a schema
                                 that declares none
"""

from __future__ import annotations

import re

import pytest
import yaml

from dashboard.common import ROOT

EXPLORE = ROOT / "dashboard" / "tabs" / "explore.py"
ML = ROOT / "dashboard" / "tabs" / "ml.py"
COMMON = ROOT / "dashboard" / "common.py"

# Names that belong to ONE schema. In code (not prose) they are coupling.
SCHEMA_SPECIFIC = ("fdm_local", "PRTY_ID", "PRTY000", "output_fdm",
                   "party.csv", "domains_fdm.yaml", "tender_events.csv")


def _code_only(path) -> str:
    """Strip docstrings and comments -- a schema name in prose is
    documentation; in code it is a hardcoded path."""
    text = path.read_text()
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return re.sub(r"#.*", "", text)


@pytest.mark.parametrize("path", [EXPLORE, ML, COMMON], ids=lambda p: p.name)
def test_no_schema_specific_names_in_dashboard_code(path):
    """CURATED_SOURCES in common.py may name its own datasets -- that is
    data, keyed by profile. Executable logic may not."""
    code = _code_only(path)
    if path.name == "common.py":
        # Drop the curated block: naming a dataset you are curating is
        # fine, it is data keyed by profile. Brace-matched rather than
        # regex'd, because the block contains nested dicts.
        start = code.find("CURATED_SOURCES")
        if start != -1:
            depth, end = 0, None
            for i in range(code.index("{", start), len(code)):
                depth += (code[i] == "{") - (code[i] == "}")
                if depth == 0:
                    end = i + 1
                    break
            assert end, "could not find the end of CURATED_SOURCES"
            code = code[:start] + code[end:]
    hits = sorted({name for name in SCHEMA_SPECIFIC if name in code})
    assert not hits, (
        f"{path.name} hardcodes schema-specific name(s) {hits} in executable code -- "
        f"the dashboard must read whatever source is selected, including schemas that "
        f"do not exist yet")


@pytest.fixture
def extra_source(tmp_path):
    """A third source, created at runtime, that the repo has never seen.

    Copies legacy_local's profile under a new name. Everything the
    dashboard derives -- label, data root, worklist path, picker, event
    feed -- must follow it without any code change."""
    new = ROOT / "config" / "profiles" / "pytest_thirdparty_local.yaml"
    raw = yaml.safe_load((ROOT / "config" / "profiles" / "legacy_local.yaml").read_text())
    raw["profile"] = "pytest_thirdparty_local"
    new.write_text(yaml.safe_dump(raw, sort_keys=False))
    try:
        yield "pytest_thirdparty_local"
    finally:
        new.unlink(missing_ok=True)


def test_a_brand_new_source_is_discovered_and_agentic(extra_source):
    from dashboard import common

    discovered = common._discover_data_sources()
    assert extra_source in discovered, "a new profile must appear with no code change"
    entry = discovered[extra_source]
    assert entry["kind"] == "full_agentic"
    assert entry["profile"] == extra_source


def test_a_brand_new_source_gets_its_own_worklist_path(extra_source):
    """Every local profile shares var/insights and the generator always
    writes the same filename, so a shared path means one source shows
    another's book."""
    from dashboard import common

    paths = {p: common.worklist_path_for(p)
             for p in [s["profile"] for s in common._discover_data_sources().values()]}
    assert len(set(paths.values())) == len(paths), f"worklist paths collide: {paths}"
    assert extra_source in str(paths[extra_source])


def test_a_brand_new_source_lists_its_own_clients(extra_source):
    """The picker must read through the new source's binding, not a fixed
    physical table."""
    from dashboard import common
    from dashboard.tabs.explore import _client_ids

    entry = common._discover_data_sources()[extra_source]
    try:
        ids = _client_ids(entry["profile"], entry["picker_concept"])
    except FileNotFoundError:
        pytest.skip("generated data not present")
    assert ids, "no clients listed for the new source"
    # legacy-shaped data -> CL* ids. The point is that it is NOT FDM's.
    assert not any(str(i).startswith("PRTY") for i in ids), (
        "the picker returned FDM client ids for a non-FDM source -- it is reading a "
        "fixed table rather than the selected source's binding")


def test_event_feed_follows_the_selected_source(extra_source):
    """A profile declaring no event_source must show none -- not FDM's."""
    from datainsights.runtime import build_runtime

    assert build_runtime(extra_source).event_source_path is None, (
        "a profile with no event_source must resolve to None, so the dashboard can say "
        "so rather than displaying another source's feed")
