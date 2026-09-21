"""
Spec-defined detectors -- a detector as YAML config rather than a Python
module (docs/signal_discovery_design.md, recommendations 1-3).

Every detector in detection_engine/*.py is one of five computational
shapes over canonical (config/semantic_model.yaml) fields. This package
implements one generic executor per shape, so a NEW signal is a spec
(archetype + canonical fields + parameters), not a new Python module.

ADDITIVE ONLY. The nine hand-written detectors are untouched and still
registered; nothing here replaces them. They remain the reference
implementation -- `SPEC_ARCHETYPES` reproduces their shapes so that a
discovered signal is expressed in the same vocabulary, not so that they
get rewritten.

Why archetypes at all, rather than letting a proposer emit Python: a
recommendation in this system has to trace to a rule a human can read and
defend (docs/decision_record.md Tab 1). Generated detector code is not
that. A spec is.

Second consumer, same foundation: each archetype here is deliberately
expressible as a window function / arithmetic filter / date diff /
anti-join / lag -- i.e. with no Python UDF -- so the same spec can later
be pushed down to SQL or Spark. That executor work is NOT BUILT; see
docs/signal_discovery_design.md's multi-backend section.
"""

from __future__ import annotations

from detection_engine.specs.archetypes import (
    ARCHETYPES,
    SignalSpec,
    SpecError,
    load_spec,
    load_specs_dir,
    run_spec,
)

__all__ = ["ARCHETYPES", "SignalSpec", "SpecError", "load_spec", "load_specs_dir", "run_spec"]
