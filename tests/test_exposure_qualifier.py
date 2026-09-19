"""
M5 verification for external_events/exposure_qualifier.py, against the
real generated FDM dataset AND its matching synthetic tender event.

The decisive test is test_same_sector_client_without_exposure_does_not_qualify
-- docs/decision_record.md's own words: "A client in the same sector
without exposure does not match. The negative case is the real test."
"""

import os
from datetime import timedelta

import pytest
import yaml

from external_events.exposure_qualifier import geography_match, load_events, qualifies, sector_match
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")
EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS_PATH)),
    reason="run `python -m data_generator.fdm.generate_fdm` and "
           "`python -m data_generator.fdm.generate_fdm_events` first",
)


@pytest.fixture
def source():
    """Named `source` for minimal test-file churn, but returns a
    CanonicalSource (docs/generalization_plan.md Phase 1) -- qualifies()/
    sector_match()/geography_match() all take a CanonicalSource now, not
    a raw FdmLocalSource."""
    return CanonicalSource(FdmLocalSource(FDM_DIR, CONTRACT_PATH), load_binding("fdm"))


@pytest.fixture
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def event():
    events = load_events(EVENTS_PATH)
    assert len(events) == 1
    return events[0]


@pytest.fixture
def as_of_review(event):
    """The exposure check for a tender award confirms revenue growth that
    follows the award, not evidence dated at-or-before it -- evaluate at
    a later 'correlation run' date, not the event's own date. See
    generate_fdm_events.py's ordering: tender dated 30 days BEFORE the
    revenue growth it's meant to explain."""
    return event.event_date + timedelta(days=90)


def test_event_loads_with_expected_shape(event):
    assert event.event_type == "public_tender_award"
    assert event.affected_sector
    assert event.affected_country


def test_sector_and_geography_match_for_a_client_in_scope(source, event):
    import pandas as pd
    demo = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_demographic.csv"))
    loc = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_locator.csv"))
    merged = demo.merge(loc, on="PRTY_ID")
    in_scope = merged[
        (merged["NACE_SECTION_CD"] == event.affected_sector)
        & (merged["COUNTRY_CD"] == event.affected_country)
    ]
    assert len(in_scope) >= 2  # the generator guarantees an exposed + unexposed pair
    for prty_id in in_scope["PRTY_ID"]:
        assert sector_match(prty_id, event, source)
        assert geography_match(prty_id, event, source)


def test_wrong_sector_never_reaches_exposure_check(source, event, rules, as_of_review):
    import pandas as pd
    demo = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_demographic.csv"))
    other_sector = demo[demo["NACE_SECTION_CD"] != event.affected_sector]
    assert not other_sector.empty
    prty_id = other_sector.iloc[0]["PRTY_ID"]
    result, magnitude = qualifies(prty_id, event, source, rules, as_of_review)
    assert result is False
    assert magnitude == 0.0


def test_same_sector_client_without_exposure_does_not_qualify(source, event, rules, as_of_review):
    """The decisive negative-case test. Finds the specific party the
    generator deliberately left unexposed (same sector+country, no
    revenue_pattern_change signal) and confirms it does NOT qualify --
    proving sector+geography match alone is not enough, exactly the
    'mailing list' failure mode the decision record warns against."""
    import pandas as pd
    demo = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_demographic.csv"))
    loc = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_locator.csv"))
    merged = demo.merge(loc, on="PRTY_ID")
    in_scope = merged[
        (merged["NACE_SECTION_CD"] == event.affected_sector)
        & (merged["COUNTRY_CD"] == event.affected_country)
    ]["PRTY_ID"].tolist()

    qualified = [pid for pid in in_scope if qualifies(pid, event, source, rules, as_of_review)[0]]
    unqualified = [pid for pid in in_scope if pid not in qualified]

    assert qualified, "expected at least one genuinely exposed party to qualify"
    assert unqualified, "expected at least one same-sector/country party to NOT qualify"


def test_genuinely_exposed_client_qualifies_with_positive_magnitude(source, event, rules, as_of_review):
    import pandas as pd
    demo = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_demographic.csv"))
    loc = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party_locator.csv"))
    merged = demo.merge(loc, on="PRTY_ID")
    in_scope = merged[
        (merged["NACE_SECTION_CD"] == event.affected_sector)
        & (merged["COUNTRY_CD"] == event.affected_country)
    ]["PRTY_ID"].tolist()

    results = [qualifies(pid, event, source, rules, as_of_review) for pid in in_scope]
    qualified_results = [r for r in results if r[0]]
    assert qualified_results
    assert all(magnitude > 0 for _, magnitude in qualified_results)


def test_unknown_event_type_raises(source, rules, event, as_of_review):
    """Reuses the real matched event's sector/country (so sector_match and
    geography_match legitimately pass and step 3 is actually reached)
    but with an event_type that has no registered exposure check."""
    from dataclasses import replace
    unknown_type_event = replace(event, event_type="natural_disaster")
    with pytest.raises(NotImplementedError, match="No exposure check"):
        qualifies("PRTY00036", unknown_type_event, source, rules, as_of_review)
