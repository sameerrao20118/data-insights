"""
De-duplication -- docs/decision_record.md Tab 3: "DE-DUPLICATION -- vs
C&I NBAs, Leads & Opportunities -- suppress or enrich existing."

This pass has no live feed of existing Pega/CRM open items to check
against (that integration is exactly what Phase 0/1 of
docs/decision_record.md require real access to build) -- so this is
scoped honestly: it de-duplicates WITHIN a single correlation run (one
Recommendation per (prty_id, nba_category), keeping the strongest) and
provides the extension point (`ExistingItemChecker` Protocol) for a real
open-items feed to plug into later, rather than pretending to check
against Pega today.
"""

from __future__ import annotations

from typing import Protocol

from datainsights.correlation.hypothesis import Recommendation


class ExistingItemChecker(Protocol):
    """Real implementation (not built this pass) would query Pega/CRM's
    open NBAs, Leads & Opportunities for this client. See
    docs/decision_record.md Tab 1 D1: Pega CDH is the live decisioning
    platform this system feeds, not duplicates."""

    def has_open_item(self, prty_id: str, category: str) -> bool: ...


class NoExistingItems:
    """Default checker when no real feed is wired -- never suppresses on
    the 'already exists elsewhere' basis, only on the within-run
    duplicate basis below. Explicit no-op, not a silent assumption."""

    def has_open_item(self, prty_id: str, category: str) -> bool:
        return False


def dedupe(recommendations: list[Recommendation], checker: ExistingItemChecker | None = None) -> list[Recommendation]:
    """Keep the strongest Recommendation per (prty_id, nba_category)
    within this run, then drop anything the checker says already has an
    open item elsewhere."""
    checker = checker or NoExistingItems()
    best_by_key: dict[tuple[str, str], Recommendation] = {}
    for rec in recommendations:
        key = (rec.prty_id, rec.nba_category)
        existing = best_by_key.get(key)
        if existing is None or rec.signal_strength > existing.signal_strength:
            best_by_key[key] = rec

    return [
        rec for rec in best_by_key.values()
        if not checker.has_open_item(rec.prty_id, rec.nba_category)
    ]
