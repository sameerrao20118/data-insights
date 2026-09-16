"""
Signal contract for cross-domain correlation (docs/decision_record.md
Tab 6, "Slot Type B -- new detector on the signal bus").

Additive only. Every existing detector's DETECTION_COLUMNS DataFrame
contract (large_incoming_payment.py, external_macro_event.py, and the new
FDM-shaped detectors in this package) is untouched -- ranking.py,
worklist.py, and digest.py all still consume that shape directly. Signal
is a separate, parallel adapter each detector module also exposes
(`to_signal(row) -> Signal`), consumed only by the correlation layer
(datainsights/correlation/, not yet built).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Signal:
    prty_id: str            # correlation key -- FDM PRTY_ID
    signal_type: str        # e.g. 'cash_buildup', 'facility_utilization_spike'
    domain: str              # deposits|lending|treasury|risk|exogenous
    direction: str           # increase|decrease|neutral
    magnitude: float         # normalised 0..1, comparable across domains
    observed_date: date
    evidence_ref: str        # table + key + effective date, for traceability
    source_tables: tuple[str, ...]
    raw_measure: dict        # domain-native value(s), kept for narrative


class Detector(Protocol):
    domain: str
    signal_type: str

    def detect(self, *args, **kwargs) -> pd.DataFrame: ...
    def to_signal(self, row: pd.Series) -> Signal: ...


VALID_DOMAINS = {"deposits", "lending", "treasury", "risk", "exogenous"}
VALID_DIRECTIONS = {"increase", "decrease", "neutral"}


def clamp_magnitude(value: float) -> float:
    """Signals must be comparable across domains -- see the decision
    record's 'normalised 0..1' requirement. Detectors compute a raw
    domain-specific ratio/z-score and pass it through this, not the other
    way around, so no detector accidentally emits an out-of-range value
    that silently dominates the correlation layer's scoring."""
    return max(0.0, min(1.0, float(value)))
