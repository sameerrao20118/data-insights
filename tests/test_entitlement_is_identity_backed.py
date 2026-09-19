"""
R21 (docs/refactor_plan.md §6j): entitlement comes from a resolved
Principal and is applied server-side; the dashboard has no RM picker.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from datainsights.config import IdentityConfig
from datainsights.identity import IdentityProviderNotRun, resolve_principal, scope_worklist
from datainsights.runtime import active_profile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _P:  # a profile stand-in with only the block identity cares about
    def __init__(self, **kw):
        self.identity = IdentityConfig(**kw)


def _book():
    return pd.DataFrame({"prty_id": ["A", "B", "C"], "relationship_manager_id": ["RM001", "RM002", "RM001"]})


def test_rm_sees_only_their_own_rows_and_nothing_without_rm_ids(monkeypatch):
    for var in ("DATAINSIGHTS_USER", "DATAINSIGHTS_ROLE", "DATAINSIGHTS_RM_IDS"):
        monkeypatch.delenv(var, raising=False)
    p = resolve_principal(_P(user="alice", role="rm", rm_ids=["RM001"]))
    assert list(scope_worklist(_book(), p)["prty_id"]) == ["A", "C"]
    none = resolve_principal(_P(user="bob", role="rm", rm_ids=[]))
    assert scope_worklist(_book(), none).empty  # deny by default


def test_supervisor_sees_the_whole_book_and_rm_sees_nothing_on_a_schema_without_rm_ids(monkeypatch):
    monkeypatch.delenv("DATAINSIGHTS_ROLE", raising=False); monkeypatch.delenv("DATAINSIGHTS_RM_IDS", raising=False)
    sup = resolve_principal(_P(user="lead", role="supervisor"))
    assert len(scope_worklist(_book(), sup)) == 3
    no_rm_col = _book().drop(columns=["relationship_manager_id"])
    rm = resolve_principal(_P(user="alice", role="rm", rm_ids=["RM001"]))
    assert scope_worklist(no_rm_col, rm).empty and len(scope_worklist(no_rm_col, sup)) == 3


def test_env_overrides_let_an_engineer_look_as_one_rm_without_editing_config(monkeypatch):
    monkeypatch.setenv("DATAINSIGHTS_ROLE", "rm"); monkeypatch.setenv("DATAINSIGHTS_RM_IDS", "RM002")
    p = resolve_principal(active_profile("fdm_local"))
    assert p.role == "rm" and p.rm_ids == frozenset({"RM002"})
    assert list(scope_worklist(_book(), p)["prty_id"]) == ["B"]


def test_idp_provider_is_declared_but_not_run():
    with pytest.raises(IdentityProviderNotRun):
        resolve_principal(_P(provider="idp"))


def test_dashboard_has_no_rm_picker():
    with open(os.path.join(REPO_ROOT, "dashboard", "tabs", "explore.py")) as f:
        src = f.read()
    assert "My RM code" not in src and 'key="fdm_rm"' not in src
    assert "scope_worklist(" in src
