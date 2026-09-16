"""
Signal Bus: collects Signal records from every detector's to_signal()
output for one as-of run, grouped by client. docs/decision_record.md Tab
3: "SIGNAL BUS -- normalised Signal record -- prty_id, domain, direction,
magnitude 0..1, evidence_ref."
"""

from __future__ import annotations

from collections import defaultdict

from detection_engine.signal import Signal


class SignalBus:
    def __init__(self):
        self._by_party: dict[str, list[Signal]] = defaultdict(list)

    def publish(self, signal: Signal) -> None:
        self._by_party[signal.prty_id].append(signal)

    def publish_all(self, signals: list[Signal]) -> None:
        for s in signals:
            self.publish(s)

    def signals_for(self, prty_id: str) -> list[Signal]:
        return list(self._by_party.get(prty_id, []))

    def all_party_ids(self) -> list[str]:
        return sorted(self._by_party.keys())

    def domains_present_for(self, prty_id: str) -> set[str]:
        return {s.domain for s in self.signals_for(prty_id)}
