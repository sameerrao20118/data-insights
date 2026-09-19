"""
fx_rate_move end-to-end proof (docs/generalization_plan.md Phase 2, R2)
-- the second event type, registered in config/event_types.yaml only,
proving qualification is genuinely declarative: no Python branch
anywhere names "fx_rate_move".

The checked-in data_generator/output_fdm/ dataset writes EUR
transactions only (deliberately -- see
tests/test_investigator_agent.py's own note on this), so there is no
real USD activity to qualify a client against without either (a)
changing the generator's default output (risking every byte-identical
regression assertion elsewhere in this repo) or (b) faking a
CanonicalSource in a way that isn't real data. This test takes a third,
honest path: it copies the real generated FDM directory to a temp
location and adds a handful of REAL-SHAPED extra transaction rows
(same columns, same AGREEMENT id, `FIN_EVNT_CURY_CD=USD`) for one
party's existing deposit account -- disclosed here, not hidden -- then
runs the actual qualifies() pipeline against that directory through a
real CanonicalSource, exactly as agents/tools.py's detector tools would.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.fdm_local import FdmLocalSource
from external_events.exposure_qualifier import ExogenousEvent, qualifies

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")

AS_AT = date(2026, 3, 1)
USD_PARTY_ACCOUNT = "AGR000001"   # PRTY00001's deposit account (party_agreement.csv)


@pytest.fixture(scope="module")
def fx_fdm_dir(tmp_path_factory):
    """A real copy of the generated FDM directory with a few extra,
    real-shaped USD transaction rows for one account -- see this file's
    module docstring."""
    dest = tmp_path_factory.mktemp("fx_fdm")
    shutil.copytree(FDM_DIR, dest, dirs_exist_ok=True)

    event_financial_path = os.path.join(dest, "kernel", "event_financial.csv")
    df = pd.read_csv(event_financial_path)
    extra_rows = [
        {"EVNT_ID": f"EVTFX{i:04d}", "AGRMNT_ID_TRN_ACCT": USD_PARTY_ACCOUNT,
         "FIN_EVNT_PSTD_DT": (AS_AT - timedelta(days=10 * (i + 1))).isoformat(),
         "FIN_EVNT_AMT": 1500.0 + 100 * i, "FIN_EVNT_CURY_CD": "USD",
         "FIN_EVNT_SBTYP_CD": "CRD", "FIN_EVNT_TYP_ID": 103}
        for i in range(3)
    ]
    df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)
    df.to_csv(event_financial_path, index=False)
    return str(dest)


@pytest.fixture(scope="module")
def canonical(fx_fdm_dir):
    return CanonicalSource(FdmLocalSource(fx_fdm_dir, CONTRACT_PATH), load_binding("fdm"))


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def fx_event():
    return ExogenousEvent(
        event_id="test-fx-1", event_date=date(2026, 2, 1), event_type="fx_rate_move",
        affected_country="", affected_sector="", severity=4, estimated_value_eur=0.0,
        payload={"currency_pair": "EUR/USD", "currency": "USD", "pct_change": -0.06, "window_days": 30},
    )


def test_party_with_real_usd_activity_qualifies(canonical, rules, fx_event):
    """PRTY00001 owns AGR000001, which now has 3 real USD-tagged
    transactions within the check's window -- must qualify, with a
    positive magnitude derived from payload.pct_change."""
    qualified, magnitude = qualifies("PRTY00001", fx_event, canonical, rules, AS_AT)
    assert qualified is True
    assert 0 < magnitude <= 1


def test_party_with_no_usd_activity_does_not_qualify(canonical, rules, fx_event):
    """PRTY00002's own transaction history is untouched (EUR only) --
    the decisive negative case: same broadcast-eligible event, but no
    genuine currency exposure of its own, so it must not qualify."""
    qualified, magnitude = qualifies("PRTY00002", fx_event, canonical, rules, AS_AT)
    assert qualified is False
    assert magnitude == 0.0


def test_magnitude_reflects_the_events_own_pct_change(canonical, rules, fx_event):
    qualified, magnitude = qualifies("PRTY00001", fx_event, canonical, rules, AS_AT)
    assert qualified is True
    assert magnitude == pytest.approx(abs(fx_event.payload["pct_change"]))
