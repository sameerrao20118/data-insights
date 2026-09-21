"""
Signal discovery driver: Stages A -> C, then a review file for a human.

    python -m onboarding.discover_signals --profile legacy_local
    python -m onboarding.discover_signals --profile legacy_local --no-llm

Stages A (enumerate) and B (screen) are deterministic and always run.
Stage C (LLM proposal) needs local Ollama and is skipped with --no-llm,
in which case the review file lists screened candidates without names or
categories -- still useful, just unnamed.

This command NEVER writes config. Acceptance is a separate, explicit step
(onboarding/signal_accept.py), the same way onboarding/propose.py and
onboarding/accept.py are separate for bindings.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta

import pandas as pd

from onboarding.signal_enumerator import enumerate_candidates, prune
from onboarding.signal_screener import co_occurrence, screen

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(_ROOT, "onboarding", "proposals", "signals")

# Which canonical concepts belong to which domain. Concepts absent here are
# skipped by the enumerator rather than guessed into a domain.
CONCEPT_DOMAIN = {
    "BalanceObservation": "deposits",
    "Transaction": "deposits",
    "Account": "lending",
    "RiskGradeVersion": "risk",
    "PartyMetricVersion": "risk",
    "CollateralValuation": "lending",
}


def _canonical(profile_name: str):
    """One CanonicalSource for the profile, plus its model config. Reading
    through CanonicalSource is what guarantees discovery sees canonical
    field names only -- never physical columns -- so a spec found here is
    portable to any schema whose binding supplies the same fields."""
    from datainsights.runtime import build_runtime
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource

    runtime = build_runtime(profile_name)
    if not runtime.binding_name:
        raise SystemExit(f"profile {profile_name!r} declares no binding -- discovery needs one")
    return CanonicalSource(runtime.source, load_binding(runtime.binding_name)), runtime


def _canonical_frames(canonical) -> dict[str, pd.DataFrame]:
    frames = {}
    for concept in CONCEPT_DOMAIN:
        if not canonical.available(concept):
            print(f"  {concept}: unavailable under this binding "
                  f"({canonical.unavailable_reason(concept)})")
            continue
        try:
            frames[concept] = canonical.read(concept)
        except Exception as e:  # noqa: BLE001 -- an unreadable concept is skipped, and said so
            print(f"  {concept}: unreadable ({type(e).__name__}: {e})")

    # Observation- and event-grain concepts are keyed by account_id, not
    # party_id (see config/semantic_model.yaml). Signals correlate on
    # party_id, so attach it from Account -- the same join the existing
    # detectors require their caller to do (see cash_buildup.py's
    # docstring: "the caller ... joins PRTY_ID before calling this").
    # original_limit/currency come along because the ratio and proximity
    # archetypes read them.
    account = frames.get("Account")
    if account is not None and "party_id" in account.columns:
        carry = [c for c in ("account_id", "party_id", "original_limit", "currency",
                              "product_class") if c in account.columns]
        lookup = account[carry].drop_duplicates(subset=["account_id"])
        for concept, df in list(frames.items()):
            if concept == "Account" or "party_id" in df.columns or "account_id" not in df.columns:
                continue
            merged = df.merge(lookup, on="account_id", how="inner", suffixes=("", "_acct"))
            dropped = len(df) - len(merged)
            frames[concept] = merged
            print(f"  {concept}: joined party_id from Account"
                  f"{f' ({dropped:,} rows dropped -- no matching account)' if dropped else ''}")
    return frames


def _existing_flagged(frames: dict[str, pd.DataFrame], as_of_dates: list[date]) -> dict[str, frozenset]:
    """Flagged-client sets for the EXISTING hand-written detectors, which
    Stage B measures novelty against.

    Rather than re-running the nine detector modules (each wants its own
    prepared frame and DetectorConfig -- that is agents/tools.py's job,
    and duplicating it here would be a second source of truth for what
    they do), this expresses each existing detector as the equivalent
    SPEC and runs it through the same archetype executors. That is the
    point of the archetype layer: if a hand-written detector cannot be
    expressed as a spec, it is not one of the five shapes, and a
    discovered candidate could not have duplicated it anyway.

    A detector whose canonical fields this binding does not supply
    contributes an empty set -- which makes the novelty test STRICTER
    (less to overlap with), never more permissive.
    """
    from detection_engine.specs.archetypes import run_spec
    from onboarding.reference_specs import reference_specs

    existing: dict[str, frozenset] = {}
    for spec in reference_specs():
        df = frames.get(spec.concept)
        if df is None or df.empty:
            existing[spec.signal_type] = frozenset()
            continue
        flagged: set[str] = set()
        for as_of in as_of_dates:
            try:
                flagged |= {s.prty_id for s in run_spec(spec, df, as_of=as_of)}
            except Exception:  # noqa: BLE001 -- unavailable here => empty => stricter novelty
                pass
        existing[spec.signal_type] = frozenset(flagged)
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover candidate signals (stages A-C).")
    parser.add_argument("--profile", default="legacy_local")
    parser.add_argument("--no-llm", action="store_true", help="skip stage C (no Ollama call)")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--as-of", default=None, help="latest as-of date (YYYY-MM-DD)")
    parser.add_argument("--max-proposals", type=int, default=12,
                        help="cap on LLM calls -- discovery is a review aid, not a sweep")
    args = parser.parse_args()

    print(f"Profile: {args.profile}")
    canonical, runtime = _canonical(args.profile)
    frames = _canonical_frames(canonical)
    if not frames:
        print("No canonical concepts available -- nothing to discover.")
        return 1
    for concept, df in frames.items():
        print(f"  {concept}: {len(df):,} rows")

    latest = date.fromisoformat(args.as_of) if args.as_of else None
    if latest is None:
        candidates_dates = []
        for df in frames.values():
            for col in df.columns:
                if col.endswith(("_at", "_date")):
                    try:
                        candidates_dates.append(pd.to_datetime(df[col]).max().date())
                    except Exception:  # noqa: BLE001
                        continue
        latest = max(candidates_dates) if candidates_dates else date.today()
    as_of_dates = [latest - timedelta(days=offset) for offset in (180, 90, 0)]
    print(f"As-of dates: {[d.isoformat() for d in as_of_dates]}")

    print("\nStage A -- enumerating candidates")
    candidates = enumerate_candidates(frames, domain_of=CONCEPT_DOMAIN)
    from datainsights.domain_registry import _all_signals
    known = set(_all_signals())
    candidates = prune(candidates, known)
    print(f"  {len(candidates)} candidates after Gate-1 pruning")
    if not candidates:
        print("  nothing eligible -- stop.")
        return 0

    print("\nStage B -- screening (deterministic, no LLM)")
    existing = _existing_flagged(frames, as_of_dates)
    results = screen(candidates, frames, as_of_dates, existing)
    passed = [r for r in results if r.passed]
    print(f"  {len(passed)} of {len(results)} candidates passed")
    for result in passed[:20]:
        print(f"    PASS {result.signal_type}: fires {result.fire_rate:.1%}, "
              f"{result.n_flagged} clients, overlap {result.max_overlap:.0%}")
    rejected_sample = [r for r in results if not r.passed][:5]
    for result in rejected_sample:
        print(f"    drop {result.signal_type}: {result.rejected_reason}")

    combos = co_occurrence({r.signal_type: r.flagged_parties for r in passed})
    if combos:
        print(f"\n  {len(combos)} candidate combination rule(s) by co-occurrence lift")
        for combo in combos[:5]:
            print(f"    {combo['when']}: lift {combo['lift']}, {combo['co_occurring_clients']} clients")

    proposals = []
    if not args.no_llm and passed:
        print(f"\nStage C -- proposing (local Ollama, max {args.max_proposals})")
        from agents.model_factory import get_model
        from onboarding.signal_proposer import propose_signals

        model = get_model(runtime.model_config)

        screens = {r.signal_type: r for r in results}
        shortlist = sorted(
            [c for c in candidates if screens.get(c.spec.signal_type, None)
             and screens[c.spec.signal_type].passed],
            key=lambda c: screens[c.spec.signal_type].max_overlap,
        )[:args.max_proposals]
        proposals = propose_signals(shortlist, screens, model)
        for proposal in proposals:
            if proposal.accepted:
                print(f"    PROPOSED {proposal.proposed_name} [{proposal.category}] "
                      f"conf={proposal.confidence:.2f}")
            else:
                print(f"    rejected {proposal.candidate_signal_type}: {proposal.rejected_reason}")

    os.makedirs(args.out, exist_ok=True)
    review_path = os.path.join(args.out, f"review_{args.profile}.json")
    with open(review_path, "w") as f:
        json.dump({
            "profile": args.profile,
            "as_of_dates": [d.isoformat() for d in as_of_dates],
            "candidates_enumerated": len(candidates),
            "candidates_passed": len(passed),
            "screened": [{"signal_type": r.signal_type, "passed": r.passed,
                          "fire_rate": r.fire_rate, "n_flagged": r.n_flagged,
                          "max_overlap": r.max_overlap,
                          "rejected_reason": r.rejected_reason} for r in results],
            "combination_candidates": combos,
            "proposals": [p.to_dict() for p in proposals],
        }, f, indent=2)
    print(f"\nReview file: {review_path}")
    print("Nothing was written to config. To accept: onboarding/signal_accept.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
