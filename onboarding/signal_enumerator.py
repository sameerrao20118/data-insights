"""
Stage A of signal discovery (docs/signal_discovery_design.md rec. 4):
enumerate candidate signal specs deterministically. NO LLM here.

For every canonical concept the active binding supplies, instantiate each
archetype over every field of a compatible type, across a small parameter
grid. Structural pruning reuses the same Gate-1 idea as
onboarding/ml_profiler.py: a measure needs enough entities and enough
observations per entity before it is worth considering at all.

What this deliberately cannot do: nominate a field the binding does not
supply, or an archetype outside detection_engine/specs/archetypes.py.
Candidates are bounded by the canonical model, so this cannot blow up
combinatorially the way free-form pattern search would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from detection_engine.specs.archetypes import SignalSpec, SpecError

# Gate 1 floors -- mirrors onboarding/ml_profiler.py's reasoning: below
# these, any pattern found is noise, and pretending otherwise is the
# fabricated-precision failure this project guards against.
MIN_ENTITIES = 20
MIN_OBSERVATIONS_PER_ENTITY = 8

# Parameter grids. Deliberately small: this is a candidate generator for
# human review, not a hyperparameter search.
DEVIATION_K = (2.1, 3.0)
DEVIATION_WINDOWS = (30, 90)
RATIO_THRESHOLDS = (0.85, 0.95)
PROXIMITY_WINDOWS = (90,)
ABSENCE_WINDOWS = (90,)

# Fields never worth treating as a business measure, regardless of dtype.
_NEVER_MEASURE = frozenset({
    "party_id", "account_id", "transaction_id", "high_risk_flag",
    "relationship_manager_id", "sector_code", "country_code",
})


@dataclass
class Candidate:
    spec: SignalSpec
    concept: str
    rationale: str                       # why enumeration produced this
    n_entities: int = 0
    observations_per_entity: float = 0.0
    eligible: bool = True
    ineligible_reason: str = ""
    # filled by Stage B
    screening: dict = field(default_factory=dict)


def _numeric_fields(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns
            if c not in _NEVER_MEASURE and pd.api.types.is_numeric_dtype(df[c])]


def _date_fields(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            out.append(c)
        elif c.endswith(("_date", "_at")) and df[c].notna().any():
            try:
                pd.to_datetime(df[c], errors="raise")
                out.append(c)
            except (ValueError, TypeError):
                continue
    return out


def _gate1(df: pd.DataFrame, grain: tuple[str, ...]) -> tuple[int, float, bool, str]:
    present = [g for g in grain if g in df.columns]
    if not present:
        return 0, 0.0, False, f"grain {list(grain)} absent from frame"
    sizes = df.groupby(list(present), sort=False).size()
    n_entities, per_entity = len(sizes), float(sizes.mean()) if len(sizes) else 0.0
    if n_entities < MIN_ENTITIES:
        return n_entities, per_entity, False, f"{n_entities} entities < {MIN_ENTITIES}"
    if per_entity < MIN_OBSERVATIONS_PER_ENTITY:
        return n_entities, per_entity, False, (
            f"{per_entity:.1f} observations/entity < {MIN_OBSERVATIONS_PER_ENTITY}")
    return n_entities, per_entity, True, ""


def _try(build) -> SignalSpec | None:
    """A spec the archetype validator rejects is simply not a candidate."""
    try:
        return build()
    except SpecError:
        return None


def enumerate_candidates(frames: dict[str, pd.DataFrame], *,
                          domain_of: dict[str, str] | None = None) -> list[Candidate]:
    """`frames` maps canonical concept name -> canonical frame (post-
    binding). `domain_of` maps concept -> domain; concepts not listed are
    skipped rather than guessed into a domain."""
    domain_of = domain_of or {}
    out: list[Candidate] = []

    for concept, df in frames.items():
        domain = domain_of.get(concept)
        if domain is None or df is None or df.empty:
            continue

        grain = tuple(g for g in ("party_id", "account_id") if g in df.columns) or ("party_id",)
        n_entities, per_entity, eligible, reason = _gate1(df, grain)
        numeric, dates = _numeric_fields(df), _date_fields(df)

        def add(spec: SignalSpec | None, rationale: str) -> None:
            if spec is None:
                return
            out.append(Candidate(spec=spec, concept=concept, rationale=rationale,
                                 n_entities=n_entities, observations_per_entity=per_entity,
                                 eligible=eligible, ineligible_reason=reason))

        for date_field in dates:
            # own-history deviation over each numeric measure
            for measure in numeric:
                for window in DEVIATION_WINDOWS:
                    for k in DEVIATION_K:
                        add(_try(lambda m=measure, d=date_field, w=window, kk=k: SignalSpec(
                            signal_type=f"{concept.lower()}_{m}_deviation_w{w}_k{str(kk).replace('.', '')}",
                            archetype="own_history_deviation", domain=domain, concept=concept,
                            grain=grain, origin="discovered", status="shadow",
                            params={"measure": m, "date_field": d, "window_days": w, "k": kk,
                                    "min_observations": MIN_OBSERVATIONS_PER_ENTITY,
                                    "median_mode": "exact"})),
                            f"{measure} deviates from its own {window}d trailing median by {k}x MAD")

            # ratio thresholds over ordered numeric pairs
            for num in numeric:
                for den in numeric:
                    if num == den:
                        continue
                    for threshold in RATIO_THRESHOLDS:
                        add(_try(lambda n=num, dd=den, d=date_field, t=threshold: SignalSpec(
                            signal_type=f"{concept.lower()}_{n}_over_{dd}_gte{str(t).replace('.', '')}",
                            archetype="ratio_threshold", domain=domain, concept=concept,
                            grain=grain, origin="discovered", status="shadow",
                            params={"numerator": n, "denominator": dd, "date_field": d,
                                    "threshold": t, "comparison": "gte"})),
                            f"{num}/{den} at or above {threshold}")

            # absence of activity
            for silent in ABSENCE_WINDOWS:
                add(_try(lambda d=date_field, s=silent: SignalSpec(
                    signal_type=f"{concept.lower()}_silent_{s}d",
                    archetype="absence", domain=domain, concept=concept, grain=grain,
                    direction="decrease", origin="discovered", status="shadow",
                    params={"date_field": d, "silent_days": s})),
                    f"no {concept} activity for {silent} days")

        # date proximity over any *other* date field
        for target in dates:
            for within in PROXIMITY_WINDOWS:
                add(_try(lambda t=target, w=within: SignalSpec(
                    signal_type=f"{concept.lower()}_{t}_within_{w}d",
                    archetype="date_proximity", domain=domain, concept=concept, grain=grain,
                    origin="discovered", status="shadow",
                    params={"date_field": t, "within_days": w})),
                    f"{target} falls within {within} days")

    return out


def prune(candidates: list[Candidate], known_signal_types: set[str]) -> list[Candidate]:
    """Drop Gate-1 failures and anything whose signal_type already exists.
    Name collision is a weak novelty check; Stage B does the real one on
    flagged-client overlap."""
    return [c for c in candidates
            if c.eligible and c.spec.signal_type not in known_signal_types]
