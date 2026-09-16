"""
AgentCore-Runtime-shaped entrypoint (docs/decision_record.md Tab 5:
"Stage 3 -- AgentCore ... Enter governance with a working artefact and
evidence"). `invoke(payload)` is a plain function; the local CLI and the
AgentCore-decorated handler both call it, so packaging for Stage 3 is
"wrap this in a container behind AgentCore Runtime," not a rewrite.

CONTRACT ONLY / NOT RUN as an actual AgentCore deployment: importing this
module is safe (no network calls, no server start) but running it as
`__main__` would start a BedrockAgentCoreApp server, which this project
has never done -- per CLAUDE.md, "AWS/Bedrock/AgentCore adapters are
contract-and-mock only until explicitly authorized." Verified this
session: `bedrock-agentcore==1.23.0` dry-run installs cleanly, matching
the version verified on the bank's Artifactory (docs/decision_record.md
Tab 7) -- the entrypoint decorator signature below is checked against the
real installed package, not guessed.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import date

import agents.tools  # noqa: F401 -- import side effect: registers every domain (agents/domain_registry.py)
from agents.domain_agent import DomainAgent
from agents.domain_registry import all_specs
from agents.model_factory import get_model
from datainsights.runtime import build_runtime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def invoke(payload: dict) -> dict:
    """payload:
      "prty_id":  str (required)
      "domain":   "all" (default) | any registered domain name (see
                  agents.domain_registry.all_specs() -- deposits, lending,
                  risk, exogenous today; a new domain added via
                  docs/adding_a_new_domain.md appears here automatically)
      "as_of":    "YYYY-MM-DD" (optional, defaults to today)
      "narrate":  bool (optional, defaults to True)
      "profile":  str (optional, defaults to "fdm_local" -- see
                  config/profiles/ and docs/generalization_plan.md Phase 0.
                  Selects source/model/event-source together; there is no
                  separate "data_dir"/"mode" override any more -- point at
                  a different profile instead.)

    domain="all" runs the full agentic pipeline (agents/orchestrator.py):
    every REGISTERED domain agent evaluates the client, the deterministic
    assembler combines their evidence, and the response carries the
    Recommendation plus each agent's result. This is the shape AgentCore
    Runtime should invoke. A single named domain returns just that
    agent's result, for debugging one domain in isolation.

    Never raises for a model/agent failure -- DomainAgent.evaluate() falls
    back to a deterministic template internally. Raises only for a
    malformed payload."""
    from agents.orchestrator import evaluate_client

    prty_id = payload["prty_id"]
    domain = payload.get("domain", "all")
    specs = all_specs()
    if domain != "all" and domain not in specs:
        raise ValueError(f"unknown domain: {domain!r} (must be 'all' or one of {list(specs)})")
    as_of = date.fromisoformat(payload["as_of"]) if payload.get("as_of") else date.today()
    narrate = bool(payload.get("narrate", True))
    profile = payload.get("profile", "fdm_local")

    rt = build_runtime(profile)
    rules, source = rt.rules, rt.source
    model = get_model(rt.model_config) if narrate else None

    if domain != "all":
        make_tools = specs[domain].make_tools
        tools = make_tools(source, rules, as_of) if domain != "exogenous" else []
        agent = DomainAgent(domain=domain, tools=tools, model=model)
        return asdict(agent.evaluate(prty_id))

    # Optional exogenous event: without one, the full pipeline still runs
    # the endogenous domain agents, but the exogenous agent -- and any
    # cross-domain confirmation it would add -- is absent. Defaults to the
    # profile's configured event_source; pass "events_path" to override it,
    # and "event_id" to pick one event out of several.
    event = None
    events_path = payload.get("events_path", rt.event_source_path)
    if events_path:
        from external_events.exposure_qualifier import load_events

        events = load_events(events_path)
        wanted = payload.get("event_id")
        matches = [e for e in events if wanted is None or e.event_id == wanted]
        if not matches:
            raise ValueError(f"event_id {wanted!r} not found in {events_path}")
        event = matches[0]

    evaluation = evaluate_client(prty_id, source=source, rules=rules, as_of=as_of,
                                  model=model, event=event, narrate=narrate)
    return {
        "prty_id": evaluation.prty_id,
        "as_of": evaluation.as_of.isoformat(),
        "recommendation": asdict(evaluation.recommendation) if evaluation.recommendation else None,
        "agent_results": {
            d: (asdict(r) if hasattr(r, "__dataclass_fields__") else r)
            for d, r in evaluation.agent_results.items()
        },
        "signal_count": len(evaluation.signals),
    }


# --- AgentCore Runtime wiring (contract only) -------------------------------
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def agentcore_entrypoint(payload: dict) -> dict:
        return invoke(payload)

except ImportError:
    app = None  # bedrock-agentcore not installed -- invoke() still works standalone


if __name__ == "__main__":
    raise SystemExit(
        "Refusing to run: `python -m agents.entrypoint` would start a real "
        "BedrockAgentCoreApp server. This is contract-only / NOT RUN per "
        "CLAUDE.md until AgentCore deployment is explicitly authorized. "
        "Call agents.entrypoint.invoke(payload) directly instead."
    )
