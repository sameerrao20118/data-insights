"""
The agentic pipeline as ONE callable unit: every domain agent evaluates
the same client, their deterministic evidence is converted to Signals,
and the correlation layer assembles one Recommendation.

Before this existed, that wiring lived only inside
agents/demo_multiagent_scenario.py -- runnable, but not reusable. A demo
is not a pipeline. This is the function AgentCore Runtime would invoke
(agents/entrypoint.py), a batch job would loop over, and a future
Snowflake-backed run would call with a different DataSource -- same code
path in all three.

Two modes, one pipeline:
  narrate=True   -- each DomainAgent makes its live LLM call and returns a
                    validated (or template-fallback) narrative. Use for a
                    handful of clients an RM will read closely.
  narrate=False  -- tools run deterministically, no LLM call at all. Use for
                    whole-book batch runs. Produces the IDENTICAL
                    Recommendation, because the recommendation never
                    depended on the LLM in the first place.

That second property is the design guarantee worth stating loudly: the
LLM narrates; it never decides the category, the score, the sizing, or
whether a recommendation exists. tests/test_orchestrator.py asserts that
both modes yield the same Recommendation for the same client.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

import agents.tools  # noqa: F401 -- import side effect: registers every domain (see agents/domain_registry.py)
from agents.investigator_agent import investigate
from agents.domain_agent import DomainAgent, DomainAgentResult
from agents.domain_registry import all_specs
from agents.domain_registry import detector_by_tool as _detector_by_tool
from datainsights.agent_trace import AgentTrace
from datainsights.agent_trace import connect as _connect_trace_db
from datainsights.agent_trace import record as _record_trace
from datainsights.correlation.hypothesis import EndogenousSizing, Recommendation, assemble
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from detection_engine.signal import Signal

# tool name -> detector module that owns its to_signal(), flattened across
# every domain registered in agents/tools.py. Adding a domain means adding
# one register() call there -- see docs/adding_a_new_domain.md.
DETECTOR_BY_TOOL = _detector_by_tool()


@dataclass
class ClientEvaluation:
    prty_id: str
    as_of: date
    agent_results: dict[str, DomainAgentResult | dict] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    recommendation: Recommendation | None = None
    exogenous_confirmed: bool = False
    # R15 (docs/refactor_plan.md §6c): set when THIS client's evaluation
    # raised. A failed client is recorded and the book continues -- one
    # bad row must never abort a scheduled run over thousands of others.
    error: str | None = None
    # R9: the Tier-2 investigator's note (agents/investigator_agent.py),
    # set only on a narrated run whose recommendation is ambiguous or
    # confirmed by more than one domain. Never on the batch path.
    investigation: object | None = None


def signals_from_tool_evidence(prty_id: str, tool_evidence: dict) -> list[Signal]:
    """Deterministic evidence -> Signal, for every endogenous detector tool
    that fired. Exogenous evidence is not a Signal; it's passed to the
    assembler as exogenous confirmation (see assemble()'s kwargs)."""
    signals = []
    for tool_name, evidence in tool_evidence.items():
        if not isinstance(evidence, dict) or evidence.get("status") != "detected":
            continue
        module = DETECTOR_BY_TOOL.get(tool_name)
        if module is None:
            continue  # exogenous or unknown tool -- not an endogenous Signal
        signal = module.to_signal(pd.Series({**evidence, "prty_id": prty_id}))
        # R17: the evidence currency rides on the Signal so sizing and
        # narration downstream never assume EUR.
        signal.raw_measure.setdefault("currency", str(evidence.get("currency", "EUR")).upper())
        signals.append(signal)
    return signals


def _high_risk_flag(canonical: CanonicalSource, prty_id: str, as_of: date) -> bool:
    """HIGH_RSK_CUST_IND -- the one permitted read. Observed here ONLY to be
    passed to assemble(), which uses it solely to suppress a revenue
    category (docs/decision_record.md, 'the one permitted read'). False
    (never an error) if the active binding doesn't provide
    Party.high_risk_flag at all -- e.g. the legacy schema -- since there
    is nothing to suppress on if the flag was never observable."""
    if not canonical.available("Party"):
        return False
    party = canonical.read("Party", as_at=as_of, party_id=prty_id)
    if party.empty or "high_risk_flag" not in party.columns:
        return False
    return bool(party.iloc[0]["high_risk_flag"])


def needs_investigation(rec) -> bool:
    """R9's two Tier-2 triggers: a disclosed-simplification category
    (`ambiguous`) or confirmation by more than one endogenous domain."""
    endogenous_domains = set(rec.confirming_domains) - {"exogenous"}
    return bool(rec.ambiguous) or len(endogenous_domains) > 1


def evaluate_client(prty_id: str, *, source, rules: dict, as_of: date,
                     model=None, event=None, narrate: bool = True,
                     trace_db_path: str | None = None, binding_name: str = "fdm",
                     canonical: CanonicalSource | None = None) -> ClientEvaluation:
    """Run every in-scope domain agent against one client and assemble a
    recommendation. `source` is any DataSource matching a
    config/bindings/<binding_name>.yaml binding -- FdmLocalSource + "fdm"
    today, any other DataSource + its own binding once one exists
    (docs/generalization_plan.md Phase 1). `event` is an optional
    ExogenousEvent to check this client's exposure against. `model` is
    required only when narrate=True. `trace_db_path`, if given, records
    one AgentTrace row per narrated domain (docs/agentic_plan.md's A0
    audit trail) -- None (the default) records nothing, so tests and
    narrate=False batch runs stay side-effect-free."""
    if narrate and model is None:
        raise ValueError("narrate=True requires a model -- see agents/model_factory.get_model()")

    # R13: a whole-book caller passes ONE shared CanonicalSource so its
    # per-(concept, as_at) frame cache and party/account index serve every
    # client; a single-client caller (Trace one client) gets a fresh one.
    if canonical is None:
        canonical = CanonicalSource(source, load_binding(binding_name))
    specs = all_specs()
    # Uniform (canonical, rules, as_of) signature for every registered
    # domain except "exogenous", whose factory needs one extra input --
    # the specific external event to check exposure against -- so it's
    # called explicitly below rather than through the generic loop. See
    # agents/tools.py's make_exogenous_tools docstring.
    domain_tools = {
        name: spec.make_tools(canonical, rules, as_of)
        for name, spec in specs.items() if name != "exogenous"
    }
    if event is not None and "exogenous" in specs:
        domain_tools["exogenous"] = specs["exogenous"].make_tools(canonical, rules, event, as_of)

    evaluation = ClientEvaluation(prty_id=prty_id, as_of=as_of)
    all_evidence: dict[str, dict] = {}

    for domain, tools in domain_tools.items():
        agent = DomainAgent(domain, tools, model)
        if narrate:
            result = agent.evaluate(prty_id)
            evaluation.agent_results[domain] = result
            all_evidence.update(result.tool_evidence)
        else:
            evidence = agent._gather_tool_evidence(prty_id)  # deterministic, no LLM
            evaluation.agent_results[domain] = evidence
            all_evidence.update(evidence)

    evaluation.signals = signals_from_tool_evidence(prty_id, all_evidence)

    exo_kwargs = {}
    exo = all_evidence.get("check_exogenous_exposure", {})
    if exo.get("status") == "detected":
        evaluation.exogenous_confirmed = True
        exo_kwargs = dict(
            exogenous_event_type=exo["event_type"],
            exogenous_event_source="TED",
            exogenous_event_date=date.fromisoformat(exo["event_date"]),
            exogenous_event_value_eur=exo["estimated_value_eur"],
        )

    evaluation.recommendation = assemble(
        prty_id, evaluation.signals, as_of=as_of,
        high_risk_flag=_high_risk_flag(canonical, prty_id, as_of),
        sizing=EndogenousSizing.from_rules_dict(rules), **exo_kwargs,
    )

    # R9: Tier 2. The deterministic fan-in above is the floor; when the
    # result is ambiguous or spans domains AND a model is present, the
    # investigator gathers this client's own evidence and PROPOSES.
    # The proposal lands on Recommendation.investigation -- nba_category
    # is never changed by it. Batch runs (narrate=False) never pay for it.
    if narrate and evaluation.recommendation is not None and needs_investigation(evaluation.recommendation):
        note = investigate(evaluation.recommendation, canonical, model, as_of, signals=evaluation.signals)
        if note is not None:
            evaluation.investigation = note
            evaluation.recommendation = replace(evaluation.recommendation, investigation={
                "proposed_category": note.proposed_category, "status": note.status,
                "reasoning": note.reasoning, "evidence_refs": list(note.evidence_refs),
                "could_not_determine": note.could_not_determine,
                "narrative_source": note.narrative_source,
            })

    if narrate and trace_db_path:
        rec_id = evaluation.recommendation.recommendation_id if evaluation.recommendation else None
        with _connect_trace_db(trace_db_path) as con:
            for domain, result in evaluation.agent_results.items():
                if not isinstance(result, DomainAgentResult):
                    continue  # narrate=False path stores a plain evidence dict, not a DomainAgentResult
                _record_trace(con, AgentTrace(
                    prty_id=prty_id, domain=domain, narrative_source=result.narrative_source,
                    latency_seconds=result.latency_seconds, fell_back=result.fell_back,
                    recommendation_id=rec_id, prompt_version=result.prompt_version,
                ))

    return evaluation


def evaluate_book(prty_ids: list[str], *, source, rules: dict, as_of: date,
                   event=None, binding_name: str = "fdm") -> list[ClientEvaluation]:
    """Whole-book batch mode -- always narrate=False. Narrating every
    client would be one LLM call per client per domain; on the 2-core,
    no-GPU bank machine documented in docs/decision_record.md Tab 7 that
    is explicitly marked 'no -- sample a handful, template the rest'.

    Per-client isolation (R15): an exception in one client's evaluation
    is captured on that client's ClientEvaluation.error and the book
    continues. Before this, evaluate_book was a bare list comprehension
    and a single bad client aborted the entire run with no partial
    results and no record of which client failed."""
    results: list[ClientEvaluation] = []
    # R13: one CanonicalSource for the whole book -- every concept is
    # assembled and projected once, every client is an index slice.
    canonical = CanonicalSource(source, load_binding(binding_name))
    for prty_id in prty_ids:
        try:
            results.append(evaluate_client(prty_id, source=source, rules=rules, as_of=as_of,
                                           event=event, narrate=False, binding_name=binding_name,
                                           canonical=canonical))
        except Exception as e:  # noqa: BLE001 -- isolation is the point; the error is recorded, not hidden
            results.append(ClientEvaluation(prty_id=prty_id, as_of=as_of,
                                            error=f"{type(e).__name__}: {e}"))
    return results
