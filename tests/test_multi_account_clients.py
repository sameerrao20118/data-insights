"""
Regression test for the single-account assumption (M16).

agents/tools.py's three deposit tools used to read dep.iloc[0] -- the
FIRST deposit account only. The FDM synthetic dataset has at most one
deposit account per client, so no existing test could observe the bug;
the legacy dataset has 251 of 300 clients with more than one account,
and SBA has two per party. A commercial client holding a current AND a
savings account had one of them silently ignored.

This stubs CanonicalSource directly rather than generating data: the
point is to construct the exact shape the shipped data cannot produce.
The flat account is deliberately FIRST, so a tool that still looked at
iloc[0] would return not_detected and fail here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from agents.tools import make_deposits_tools

AS_OF = date(2025, 1, 1)
FLAT_ACCOUNT = "ACC_FLAT"
RISING_ACCOUNT = "ACC_RISING"


@pytest.fixture(scope="module")
def rules():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config", "rules.yaml")) as f:
        return yaml.safe_load(f)


def _weekly_balances(account_id: str, *, rising: bool) -> pd.DataFrame:
    """26 weekly observations ending at AS_OF. Rising compounds 2%/week,
    which clears cash_buildup's 15%-over-60-days gate with margin."""
    rows = []
    balance = 100_000.0
    for week in range(26, -1, -1):
        observed = AS_OF - timedelta(days=7 * week)
        rows.append({"account_id": account_id, "observed_at": observed.isoformat(),
                     "balance": round(balance, 2)})
        if rising:
            balance *= 1.02
    return pd.DataFrame(rows)


class _StubCanonical:
    """Only the concepts the deposit tools actually read."""

    def __init__(self, accounts: pd.DataFrame, balances: pd.DataFrame):
        self._accounts, self._balances = accounts, balances

    def available(self, concept):  # noqa: D102
        return True

    def read(self, concept, *, as_at=None, party_id=None, account_id=None):
        if concept == "Account":
            df = self._accounts
            if party_id is not None:
                df = df[df["party_id"] == party_id]
            return df.reset_index(drop=True)
        if concept == "BalanceObservation":
            df = self._balances
            if account_id is not None:
                df = df[df["account_id"] == account_id]
            return df.reset_index(drop=True)
        if concept == "Transaction":
            return pd.DataFrame(columns=["transaction_id", "account_id", "posted_at",
                                         "amount", "direction", "currency"])
        raise AssertionError(f"unexpected concept read: {concept}")


def _canonical_with(first_rising: bool, second_rising: bool) -> _StubCanonical:
    accounts = pd.DataFrame([
        {"account_id": FLAT_ACCOUNT, "party_id": "P1", "product_class": "deposit"},
        {"account_id": RISING_ACCOUNT, "party_id": "P1", "product_class": "deposit"},
    ])
    balances = pd.concat([
        _weekly_balances(FLAT_ACCOUNT, rising=first_rising),
        _weekly_balances(RISING_ACCOUNT, rising=second_rising),
    ], ignore_index=True)
    return _StubCanonical(accounts, balances)


def _cash_buildup_tool(canonical, rules):
    tools = make_deposits_tools(canonical, rules, AS_OF)
    return next(t for t in tools if "cash_buildup" in str(getattr(t, "tool_name", "")))


def test_buildup_on_the_second_deposit_account_is_found(rules):
    """The actual regression: first account flat, second rising. Before
    the fix this returned not_detected because only iloc[0] was read."""
    tool = _cash_buildup_tool(_canonical_with(first_rising=False, second_rising=True), rules)
    result = tool("P1")
    assert result["status"] == "detected", result
    assert result["agrmnt_id"] == RISING_ACCOUNT, (
        f"detected on {result['agrmnt_id']!r} -- the buildup is on {RISING_ACCOUNT!r}")


def test_buildup_on_the_first_deposit_account_still_found(rules):
    """Control: the fix must not trade one account for another."""
    tool = _cash_buildup_tool(_canonical_with(first_rising=True, second_rising=False), rules)
    result = tool("P1")
    assert result["status"] == "detected", result
    assert result["agrmnt_id"] == FLAT_ACCOUNT


def test_no_buildup_on_either_account_is_not_detected(rules):
    """Negative case -- two accounts must not manufacture a detection."""
    tool = _cash_buildup_tool(_canonical_with(first_rising=False, second_rising=False), rules)
    assert tool("P1")["status"] == "not_detected"
