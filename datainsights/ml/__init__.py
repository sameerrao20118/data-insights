"""
ML challenger slots -- docs/decision_record.md Tab 6, "Slot Type E: ML
challengers, behind explicit interfaces".

The ordering discipline the decision record insists on, and this package
enforces structurally:

    "E1 Normaliser: Explicit documented function first. Auditable
     baseline stays as the challenger benchmark."

So every slot ships a DETERMINISTIC default that is the production path,
plus an optional ML challenger that must be explicitly selected. Nothing
silently upgrades itself to a model. This matches CLAUDE.md's
"Deterministic first, ML only when justified" and keeps every number in
an RM's worklist traceable to either an explicit formula or a named,
opted-into model.

What is deliberately NOT a slot here, quoting the decision record:
"Not a slot: arbitration or final ranking. Pega adaptive models own that
and already learn from responses across all NBAs. A competing ranker
fragments the learning signal and creates two systems disagreeing about
priority." There is no ranker in this package and there should never be
one -- see datainsights/correlation/ for the deterministic assembler that
deliberately stops short of arbitration.
"""

from datainsights.ml.slots import (  # noqa: F401
    BaselineModel,
    DeterministicBaseline,
    ExposureQualifierModel,
    InsufficientHistory,
    MagnitudeNormaliser,
    PropensityModel,
    ProportionalNormaliser,
    UnavailablePropensityModel,
)
