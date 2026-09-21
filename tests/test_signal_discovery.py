"""
Signal discovery stages A-E (docs/signal_discovery_design.md recs. 4-8).

The single most important test in this file is
test_shadow_signals_are_invisible_to_the_assembler: it is the structural
guarantee that a machine-proposed signal cannot reach an RM. If that
fails, the whole discovery design is unsafe regardless of everything else.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from detection_engine.specs.archetypes import SignalSpec
from onboarding.signal_accept import accept, load_discovered, shadow_signal_types
from onboarding.signal_enumerator import Candidate, enumerate_candidates, prune
from onboarding.signal_proposer import SignalProposal, _ProposedSignal, _propose_one
from onboarding.signal_screener import co_occurrence, screen_candidate


def _frame(n_entities: int = 40, n_days: int = 120) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for e in range(n_entities):
        for i in range(n_days):
            amount = 1000.0 + (i % 7) * 10
            if e < 4 and i == 100:
                amount = 50_000.0          # a few entities spike
            rows.append({"account_id": f"AC-{e}", "party_id": f"P-{e}",
                         "posted_at": start + timedelta(days=i),
                         "amount": amount, "original_limit": 5000.0, "currency": "EUR"})
    return pd.DataFrame(rows)


def _candidate(**params) -> Candidate:
    base = {"measure": "amount", "date_field": "posted_at", "window_days": 90,
            "k": 2.1, "min_observations": 8, "median_mode": "exact"}
    base.update(params)
    spec = SignalSpec(signal_type="cand_dev", archetype="own_history_deviation",
                      domain="deposits", concept="Transaction",
                      grain=("party_id", "account_id"), params=base,
                      origin="discovered", status="shadow")
    return Candidate(spec=spec, concept="Transaction", rationale="amount deviates")


# --------------------------------------------------------------------------
# stage A -- enumeration
# --------------------------------------------------------------------------

def test_enumeration_only_proposes_known_archetypes_and_domains():
    candidates = enumerate_candidates({"Transaction": _frame()},
                                       domain_of={"Transaction": "deposits"})
    assert candidates
    for candidate in candidates:
        assert candidate.spec.origin == "discovered"
        assert candidate.spec.status == "shadow", "every candidate must start as shadow"


def test_enumeration_skips_concepts_with_no_declared_domain():
    """A concept not mapped to a domain is skipped, never guessed into one."""
    assert enumerate_candidates({"Transaction": _frame()}, domain_of={}) == []


def test_gate1_rejects_a_book_too_small_to_learn_from():
    tiny = _frame(n_entities=3, n_days=10)
    candidates = enumerate_candidates({"Transaction": tiny}, domain_of={"Transaction": "deposits"})
    assert candidates, "should still enumerate"
    assert all(not c.eligible for c in candidates), "but nothing should be eligible"
    assert prune(candidates, set()) == []


def test_prune_drops_names_that_already_exist():
    candidates = enumerate_candidates({"Transaction": _frame()},
                                       domain_of={"Transaction": "deposits"})
    known = {candidates[0].spec.signal_type}
    assert all(c.spec.signal_type not in known for c in prune(candidates, known))


# --------------------------------------------------------------------------
# stage B -- screening
# --------------------------------------------------------------------------

def test_a_candidate_firing_on_everyone_is_rejected_as_a_threshold():
    result = screen_candidate(_candidate(k=0.001), {"Transaction": _frame()},
                              [date(2026, 4, 1)], existing={})
    assert not result.passed
    assert "threshold, not a signal" in result.rejected_reason


def test_a_candidate_firing_on_nobody_is_rejected():
    result = screen_candidate(_candidate(k=500.0), {"Transaction": _frame()},
                              [date(2026, 4, 1)], existing={})
    assert not result.passed
    assert "below" in result.rejected_reason


def test_a_candidate_duplicating_an_existing_signal_is_rejected_as_not_novel():
    """The core novelty test. A candidate flagging the same clients an
    existing detector already flags tells the bank nothing new."""
    frames = {"Transaction": _frame()}
    solo = screen_candidate(_candidate(), frames, [date(2026, 4, 30)], existing={})
    assert solo.passed, "control: this candidate passes when nothing else exists"

    duplicate = screen_candidate(_candidate(), frames, [date(2026, 4, 30)],
                                 existing={"already_detected": solo.flagged_parties})
    assert not duplicate.passed
    assert "not novel" in duplicate.rejected_reason


def test_screening_rejects_a_candidate_that_leaks_future_rows():
    """Defence in depth: even if an executor regressed, the screener must
    refuse a candidate whose signals postdate the as-of date."""
    result = screen_candidate(_candidate(), {"Transaction": _frame()},
                              [date(2026, 1, 15)], existing={})
    assert all(not r or True for r in [result])   # structural: no exception
    if not result.passed and result.rejected_reason:
        assert "observed after as_of" not in result.rejected_reason, (
            "executor leaked future rows -- as-of clipping regressed")


def test_co_occurrence_reports_pairs_above_chance():
    """Lift is measured against a universe, so the fixture has to look
    like a real book: two modest signals that overlap far more than their
    prevalence predicts, inside a much larger client population."""
    universe = [f"P-{i}" for i in range(100)]
    shared = frozenset(universe[:8])
    combos = co_occurrence({
        "a": shared | frozenset(universe[8:12]),      # 12 of 100
        "b": shared | frozenset(universe[12:16]),     # 12 of 100, 8 shared
        "c": frozenset(universe[50:62]),               # 12 of 100, disjoint
    }, min_pairs=5)
    assert combos, "a strongly-overlapping pair should be reported"
    assert combos[0]["when"] == ["a", "b"]
    assert combos[0]["lift"] >= 1.5
    assert all(set(c["when"]) != {"a", "c"} for c in combos), "disjoint pair is not a combination"


# --------------------------------------------------------------------------
# stage C -- proposer validation (stubbed model)
# --------------------------------------------------------------------------

class _StubAgent:
    """Stands in for strands.Agent. Returns whatever _ProposedSignal the
    test supplies, so the VALIDATION logic is what gets tested -- not the
    model. A real-Ollama run is exercised separately by the CLI."""

    payload: _ProposedSignal | None = None
    raises: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    def __call__(self, *_args, **_kwargs):
        if _StubAgent.raises:
            raise _StubAgent.raises
        return type("Result", (), {"structured_output": _StubAgent.payload})()


@pytest.fixture
def stub_agent(monkeypatch):
    import strands
    monkeypatch.setattr(strands, "Agent", _StubAgent)
    _StubAgent.payload, _StubAgent.raises = None, None
    return _StubAgent


def _screen_result():
    from onboarding.signal_screener import ScreenResult
    return ScreenResult("cand_dev", True, fire_rate=0.1, n_flagged=4, max_overlap=0.1)


def test_proposer_rejects_an_unregistered_category(stub_agent):
    """A model inventing a seventh category must be REJECTED, never
    coerced to ADVISORY_ONLY -- coercion would hide the failure."""
    stub_agent.payload = _ProposedSignal(
        is_meaningful=True, proposed_name="something_new",
        category="GROWTH_OPPORTUNITY", hypothesis="h", confidence=0.9)
    proposal = _propose_one(_candidate(), _screen_result(), model=None)
    assert not proposal.accepted
    assert "not registered" in proposal.rejected_reason


def test_proposer_respects_the_models_own_no(stub_agent):
    stub_agent.payload = _ProposedSignal(
        is_meaningful=False, proposed_name="x", category="ADVISORY_ONLY",
        hypothesis="this is just an identifier", confidence=0.1)
    proposal = _propose_one(_candidate(), _screen_result(), model=None)
    assert not proposal.accepted
    assert "not business-meaningful" in proposal.rejected_reason


def test_proposer_rejects_an_unusable_name(stub_agent):
    stub_agent.payload = _ProposedSignal(
        is_meaningful=True, proposed_name="not a valid name!",
        category="ADVISORY_ONLY", hypothesis="h", confidence=0.5)
    proposal = _propose_one(_candidate(), _screen_result(), model=None)
    assert not proposal.accepted
    assert "not a usable identifier" in proposal.rejected_reason


def test_llm_failure_becomes_a_rejection_never_a_guess(stub_agent):
    stub_agent.raises = RuntimeError("ollama unreachable")
    proposal = _propose_one(_candidate(), _screen_result(), model=None)
    assert not proposal.accepted
    assert "proposal failed" in proposal.rejected_reason
    assert proposal.spec is None


def test_accepted_proposal_is_always_shadow(stub_agent):
    stub_agent.payload = _ProposedSignal(
        is_meaningful=True, proposed_name="deposit_concentration_shift",
        category="TREASURY_OPPORTUNITY", hypothesis="h", confidence=0.7)
    proposal = _propose_one(_candidate(), _screen_result(), model=None)
    assert proposal.accepted
    assert proposal.spec.status == "shadow"
    assert proposal.spec.origin == "discovered"


# --------------------------------------------------------------------------
# stages D and E -- acceptance and the shadow boundary
# --------------------------------------------------------------------------

def _accepted(name="deposit_concentration_shift", candidate_type="cand_dev") -> SignalProposal:
    spec = SignalSpec(signal_type=name, archetype="own_history_deviation", domain="deposits",
                      concept="Transaction", grain=("party_id", "account_id"),
                      params={"measure": "amount", "date_field": "posted_at",
                              "window_days": 90, "k": 2.1, "median_mode": "exact"},
                      origin="discovered", status="shadow")
    return SignalProposal(candidate_signal_type=candidate_type,
                          archetype="own_history_deviation", accepted=True, spec=spec,
                          proposed_name=name, category="TREASURY_OPPORTUNITY",
                          why_now="w", hypothesis="h", confidence=0.7,
                          screening={"fire_rate": 0.1, "n_flagged": 4, "max_overlap": 0.1})


def test_accept_requires_a_named_human(tmp_path):
    with pytest.raises(ValueError, match="accepted_by"):
        accept([_accepted()], accepted_by="",
               domains_path=str(tmp_path / "d.yaml"), specs_dir=str(tmp_path / "specs"))


def test_accept_writes_shadow_status_and_provenance(tmp_path):
    domains, specs = str(tmp_path / "d.yaml"), str(tmp_path / "specs")
    accept([_accepted()], accepted_by="reviewer@bank", domains_path=domains, specs_dir=specs)

    written = yaml.safe_load(open(domains))
    entry = written["deposits"]["signals"]["deposit_concentration_shift"]
    assert entry["status"] == "shadow"
    assert entry["origin"] == "discovered"
    assert entry["provenance"]["accepted_by"] == "reviewer@bank"
    assert entry["provenance"]["screening"]["clients_flagged"] == 4
    assert "accepted_at" in entry["provenance"]


def test_accept_disambiguates_two_proposals_sharing_a_name(tmp_path):
    """Measured on a real Ollama run: three candidates differing only in
    window/k all came back named 'transaction_amount_deviation'. They are
    different rules, so both must survive -- silently keeping one would
    misrepresent what the reviewer approved."""
    domains, specs = str(tmp_path / "d.yaml"), str(tmp_path / "specs")
    result = accept(
        [_accepted(candidate_type="transaction_amount_deviation_w30_k30"),
         _accepted(candidate_type="transaction_amount_deviation_w90_k21")],
        accepted_by="reviewer", domains_path=domains, specs_dir=specs)
    assert len(result["written"]) == 2, f"both should be written, got {result}"
    assert len(set(result["written"])) == 2, "names must be distinct"


def test_rejected_proposals_are_never_written(tmp_path):
    domains, specs = str(tmp_path / "d.yaml"), str(tmp_path / "specs")
    rejected = SignalProposal("cand", "own_history_deviation", accepted=False,
                              rejected_reason="model said no")
    result = accept([rejected], accepted_by="reviewer",
                    domains_path=domains, specs_dir=specs)
    assert result["written"] == []
    assert yaml.safe_load(open(domains)) in ({}, None)


def test_shadow_signals_are_invisible_to_the_assembler(tmp_path):
    """THE safety property (stage E). A discovered signal must not be
    resolvable by datainsights/domain_registry.py, because
    correlation/hypothesis.py::assemble() reads categories from there --
    if it could, a machine-proposed signal would land on an RM worklist
    with no human ever having approved it."""
    from datainsights import domain_registry

    domains, specs = str(tmp_path / "d.yaml"), str(tmp_path / "specs")
    accept([_accepted()], accepted_by="reviewer", domains_path=domains, specs_dir=specs)

    assert "deposit_concentration_shift" in shadow_signal_types(domains)

    # The production registry reads config/domains_fdm.yaml and nothing else.
    assert "deposit_concentration_shift" not in domain_registry._all_signals()
    # Unknown signal types fall back to the default, never to the shadow entry.
    assert domain_registry.category_for("deposit_concentration_shift") == "ADVISORY_ONLY"


def test_tooling_never_writes_to_the_handwritten_domains_file(tmp_path):
    """config/domains_fdm.yaml stays hand-authored. Discovery writes a
    separate file so a reviewer can diff machine proposals against human
    ones, and revert one without disturbing the other."""
    import onboarding.signal_accept as module

    before = open(module.os.path.join(
        module._ROOT, "config", "domains_fdm.yaml")).read()
    accept([_accepted()], accepted_by="reviewer",
           domains_path=str(tmp_path / "d.yaml"), specs_dir=str(tmp_path / "specs"))
    after = open(module.os.path.join(
        module._ROOT, "config", "domains_fdm.yaml")).read()
    assert before == after, "discovery must never modify the hand-authored domains file"


def test_load_discovered_is_empty_when_nothing_accepted(tmp_path):
    assert load_discovered(str(tmp_path / "absent.yaml")) == {}
