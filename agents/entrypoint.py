"""
AgentCore-Runtime-shaped entrypoint (docs/decision_record.md Tab 5:
"Stage 3 -- AgentCore ... Enter governance with a working artefact and
evidence"). `invoke(payload)` is a plain function; the local CLI and the
AgentCore-decorated handler both call it, so packaging for Stage 3 is
"wrap this in a container behind AgentCore Runtime," not a rewrite.

AgentCore deployment authorized 2026-10-23 (docs/aws_deployment.md):
infra/terraform/stacks/30-agents runs this module as the container's
process with DATAINSIGHTS_AGENTCORE_SERVE=1 and DATAINSIGHTS_PROFILE=fdm_agentcore.
Importing it stays side-effect free; `__main__` without that key refuses.
NOT RUN on AWS until you apply the stacks. Verified this
session: `bedrock-agentcore==1.23.0` dry-run installs cleanly, matching
the version verified on the bank's Artifactory (docs/decision_record.md
Tab 7) -- the entrypoint decorator signature below is checked against the
real installed package, not guessed.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

import agents.tools  # noqa: F401 -- import side effect: registers every domain (agents/domain_registry.py)
from agents.domain_agent import DomainAgent
from agents.domain_registry import all_specs
from agents.model_factory import get_model
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def invoke(payload: dict) -> dict:
    """payload:
      "action":   "client" (default) | "book" -- "book" is a whole-book run,
                  see invoke_book() below; every other key here is "client"'s
      "prty_id":  str (required)
      "domain":   "all" (default) | any registered domain name (see
                  agents.domain_registry.all_specs() -- deposits, lending,
                  risk, exogenous today; a new domain added via
                  docs/adding_a_new_domain.md appears here automatically)
      "as_of":    "YYYY-MM-DD" (optional, defaults to today)
      "narrate":  bool (optional, defaults to True)
      "profile":  str (optional, defaults to $DATAINSIGHTS_PROFILE, else "fdm_local" -- see
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

    if payload.get("action", "client") == "book":
        return invoke_book(payload)
    prty_id = payload["prty_id"]
    domain = payload.get("domain", "all")
    specs = all_specs()
    if domain != "all" and domain not in specs:
        raise ValueError(f"unknown domain: {domain!r} (must be 'all' or one of {list(specs)})")
    as_of = date.fromisoformat(payload["as_of"]) if payload.get("as_of") else date.today()
    narrate = bool(payload.get("narrate", True))
    profile = payload.get("profile") or os.environ.get("DATAINSIGHTS_PROFILE", "fdm_local")

    rt = build_runtime(profile)
    rules, source = rt.rules, rt.source
    model = get_model(rt.model_config) if narrate else None

    if domain != "all":
        canonical = CanonicalSource(source, load_binding(rt.binding_name or "fdm"))
        make_tools = specs[domain].make_tools
        tools = make_tools(canonical, rules, as_of) if domain != "exogenous" else []
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
                                  model=model, event=event, narrate=narrate,
                                  binding_name=rt.binding_name or "fdm")
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


def invoke_book(payload: dict) -> dict:
    """A recorded whole-book run (datainsights/runs.py::run_book), always
    narrate=False (evaluate_book's rule: no LLM call per client). On the
    AgentCore profile the worklist, digest and MIMO-shaped insights land in
    S3 (<insights>/<as_of>/<run_id>/ and <insights>/latest/) and the run is
    a DynamoDB ledger item guarded by a cross-session lock. Always a FULL
    run on AWS -- see datainsights/aws_backends.py for why.

    payload: {"action": "book", "profile": ..., "as_of": "YYYY-MM-DD" (optional),
              "top": int (rows of the worklist to return inline, default 10)}"""
    from datainsights.runs import run_book
    from datainsights.runtime import active_profile
    from datainsights.sinks.mimo_placeholder import write_insights

    profile_name = payload.get("profile") or os.environ.get("DATAINSIGHTS_PROFILE", "fdm_local")
    as_of = date.fromisoformat(payload["as_of"]) if payload.get("as_of") else None
    profile = active_profile(profile_name)
    ledger = None
    lock_id = f"pending-{os.getpid()}-{id(payload)}"
    if profile.state.backend == "dynamodb":
        from datainsights.aws_backends import RunLedger

        ledger = RunLedger(profile.state.table_name)
        ledger.acquire(profile_name, lock_id)
    try:
        result = run_book(profile_name, as_of=as_of, incremental=False, events_path=None)
        out = dict(result.out_paths)
        if out.get("worklist"):
            out["insights"] = write_insights(result.recommendations, os.path.join(
                os.path.dirname(out["worklist"]), "fdm_insights.json"), as_of=result.as_of)
        if profile.output.backend == "s3":
            from datainsights.aws_backends import publish_files

            out = publish_files(out, profile.output.s3_uri, as_of=result.as_of.isoformat(), run_id=result.run_id)
        summary = {"run_id": result.run_id, "as_of": result.as_of.isoformat(),
                   "n_clients": len(result.evaluated_ids) + len(result.carried_ids),
                   "n_recommendations": len(result.recommendations), "seconds": round(result.seconds, 2),
                   "outputs": out}
        if ledger:
            ledger.record(profile_name, result.run_id, status="finished", summary=summary)
    except Exception as e:
        if ledger:
            ledger.record(profile_name, lock_id, status="failed", summary={"error": f"{type(e).__name__}: {e}"})
        raise
    finally:
        if ledger:
            ledger.release(profile_name, lock_id)
    top = int(payload.get("top", 10))
    summary["worklist_top"] = json.loads(result.worklist.head(top).to_json(orient="records", date_format="iso"))
    return summary


# --- AgentCore Runtime wiring ------------------------------------------------
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def agentcore_entrypoint(payload: dict) -> dict:
        return invoke(payload)

except ImportError:
    app = None  # bedrock-agentcore not installed -- invoke() still works standalone


SERVE_KEY = "DATAINSIGHTS_AGENTCORE_SERVE"


if __name__ == "__main__":
    # The container's CMD (Dockerfile). Serving is authorized for the
    # AgentCore deployment (docs/aws_deployment.md); the key is set by
    # infra/terraform on the runtime, so `python -m agents.entrypoint` on a
    # laptop still refuses rather than opening a port by accident.
    if os.environ.get(SERVE_KEY) != "1" or app is None:
        raise SystemExit(
            f"Refusing to serve: {SERVE_KEY}=1 is not set (or bedrock-agentcore is not installed). "
            "This starts the AgentCore HTTP server and is meant for the deployed container only. "
            "Locally, call agents.entrypoint.invoke(payload) instead.")
    app.run()
