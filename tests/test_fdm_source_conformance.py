"""
Conformance between FdmLocalSource and FdmSnowflakeSource.

This is the test that makes the "swapping to Snowflake won't be a
struggle" claim checkable rather than aspirational. The legacy pipeline
has the same gap documented as an open item in docs/architecture.md ("No
golden conformance tests comparing OfflineLocalSource and SnowflakeSource
output on identical data") -- this closes it for the FDM path, at the
level that's actually verifiable without credentials.

What this CAN verify without a live Snowflake account: that both classes
expose the same method surface with the same signatures, honour the same
contract file, resolve the same `domain:` tag (to a folder vs. a schema),
and fail closed in the same places.

What it CANNOT verify, and nobody should claim it does: that a real
Snowflake query returns identical DATA to the local CSVs. That needs
credentials and an actual run on the VDI -- see FdmSnowflakeSource's
"STATUS: NOT RUN" docstring.
"""

import inspect
import os

import pytest
import yaml

from datainsights.sources.base import DataSourceError
from datainsights.sources.fdm_local import FdmLocalSource
from datainsights.sources.fdm_snowflake import FdmSnowflakeSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

# Every method the layers above the DataSource actually call. If a new one
# is added to the local source and not the Snowflake one, the swap breaks
# at runtime on the VDI -- this list is what prevents that.
SHARED_SURFACE = [
    "capabilities", "read_entity", "as_at", "party", "agreement",
    "daily_balance", "financial_event", "party_demographic", "party_locator",
    "collateral_item_value", "risk_measure", "treasury_position", "party_group",
]

DOMAIN_SCHEMA_MAP = {"kernel": "ENT_PRD.TIER0_PRS", "lending": "ENT_PRD.NPDM_LENDING"}


@pytest.fixture
def snowflake_source():
    """Constructing this must NOT connect to anything -- the connection is
    lazy. If this fixture ever starts requiring credentials, that's a
    regression in the fail-closed design, not a test environment problem."""
    return FdmSnowflakeSource("poc", CONTRACT_PATH, DOMAIN_SCHEMA_MAP)


@pytest.mark.parametrize("method_name", SHARED_SURFACE)
def test_both_sources_expose_the_same_method(method_name):
    assert hasattr(FdmLocalSource, method_name), f"FdmLocalSource lacks {method_name}"
    assert hasattr(FdmSnowflakeSource, method_name), f"FdmSnowflakeSource lacks {method_name}"


@pytest.mark.parametrize("method_name", [
    m for m in SHARED_SURFACE if m not in ("read_entity", "capabilities")
])
def test_shared_methods_have_identical_signatures(method_name):
    """Signature drift is the failure mode that would only surface on the
    VDI, halfway through a migration -- catch it here instead."""
    local_sig = inspect.signature(getattr(FdmLocalSource, method_name))
    snow_sig = inspect.signature(getattr(FdmSnowflakeSource, method_name))
    assert local_sig == snow_sig, (
        f"{method_name} differs:\n  local:     {local_sig}\n  snowflake: {snow_sig}"
    )


def test_snowflake_source_constructs_without_connecting(snowflake_source):
    assert snowflake_source._con is None
    assert snowflake_source.capabilities().backend == "fdm_snowflake"
    assert snowflake_source.capabilities().read_only is True


def test_snowflake_refuses_unmapped_domain():
    """Fail closed: a contract domain with no schema mapping must raise at
    construction, not silently read from a default schema."""
    with pytest.raises(DataSourceError, match="No Snowflake schema mapped"):
        FdmSnowflakeSource("poc", CONTRACT_PATH, {"kernel": "ENT_PRD.TIER0_PRS"})


def test_snowflake_refuses_fincrime_schema_mapping():
    with pytest.raises(DataSourceError, match="FinCrime"):
        FdmSnowflakeSource("poc", CONTRACT_PATH,
                            {"kernel": "FSA_PRD_FINCRIME", "lending": "ENT_PRD.NPDM_LENDING"})


def test_domain_tag_resolves_to_schema_not_folder(snowflake_source):
    """The core of the extensibility claim: the SAME `domain:` tag that
    FdmLocalSource turns into a folder, this turns into a schema."""
    assert snowflake_source._qualified_table("PARTY") == "ENT_PRD.TIER0_PRS.party"
    assert snowflake_source._qualified_table("MORTGAGE_AGREEMENT") == "ENT_PRD.NPDM_LENDING.mortgage_agreement"


def test_both_sources_read_the_same_contract_file():
    """Neither source may carry its own private entity list -- one
    contract, two backends."""
    with open(CONTRACT_PATH) as f:
        contract_entities = set(yaml.safe_load(f)["entities"])
    snow = FdmSnowflakeSource("poc", CONTRACT_PATH, DOMAIN_SCHEMA_MAP)
    assert set(snow._contract["entities"]) == contract_entities
    if os.path.isdir(FDM_DIR):
        local = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
        assert set(local._contract["entities"]) == contract_entities


@pytest.mark.parametrize("slot_method,expected", [
    ("risk_measure", "SLOT A1"),
    ("treasury_position", "SLOT A2"),
])
def test_unavailable_slots_fail_identically_on_both_sources(snowflake_source, slot_method, expected):
    """A deferred/blocked slot must be deferred on BOTH backends -- if
    Snowflake silently returned an empty frame where local raises, a
    migration would turn 'we have no data for this' into 'this client has
    none', which is a data-integrity hazard, not a cosmetic difference."""
    from datetime import date
    with pytest.raises(NotImplementedError, match=expected):
        getattr(snowflake_source, slot_method)(date(2026, 1, 1))
    if os.path.isdir(FDM_DIR):
        local = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
        with pytest.raises(NotImplementedError, match=expected):
            getattr(local, slot_method)(date(2026, 1, 1))
