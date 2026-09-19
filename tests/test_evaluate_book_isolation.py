"""R15 (docs/refactor_plan.md §6c): one client's failure must not abort
the book. Before the fix evaluate_book() was a list comprehension -- a
single exception lost every other client's result and gave no record of
which client failed."""

from __future__ import annotations

from datetime import date

import agents.orchestrator as orch


def test_one_failing_client_does_not_abort_the_book(monkeypatch):
    calls = []

    def fake_evaluate_client(prty_id, **kwargs):
        calls.append(prty_id)
        if prty_id == "P_BAD":
            raise RuntimeError("simulated detector crash")
        return orch.ClientEvaluation(prty_id=prty_id, as_of=kwargs["as_of"])

    monkeypatch.setattr(orch, "evaluate_client", fake_evaluate_client)
    out = orch.evaluate_book(["P1", "P_BAD", "P3"], source=None, rules={}, as_of=date(2025, 1, 1))

    assert calls == ["P1", "P_BAD", "P3"], "the book must continue past the failure"
    assert [e.prty_id for e in out] == ["P1", "P_BAD", "P3"]
    bad = out[1]
    assert bad.error and "simulated detector crash" in bad.error
    assert bad.recommendation is None
    assert out[0].error is None and out[2].error is None
