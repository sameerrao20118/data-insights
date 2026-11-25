"""
get_model(cfg) -- exactly the swap point docs/decision_record.md Tab 5
("AgentCore Staging -- D4 In Practice") specifies:

    def get_model(cfg):
        if cfg.mode == "local":
            from strands.models.ollama import OllamaModel
            return OllamaModel(host="http://localhost:11434", model_id=cfg.model_id)
        from strands.models.bedrock import BedrockModel
        return BedrockModel(model_id=cfg.model_id)

"Put the provider behind this factory from day one. Existing non-localhost
validators stay active in local mode and are relaxed at Stage 3 as a
documented decision."

This implementation routes the local branch through
datainsights.config.LLMConfig's existing validator (rejects "-cloud"
Ollama tags and non-localhost hosts) BEFORE constructing anything --
the same safety check narrative/ollama_narrator.py already benefits from,
reused rather than reimplemented.

The "bedrock" branch was authorized on 2026-10-23 for the AgentCore
deployment only (docs/aws_deployment.md). It needs two independent keys:
the profile must be an agentcore profile carrying a dated
cost.paid_authorization (datainsights/config.py refuses anything else), and
the process must have DATAINSIGHTS_PAID_AWS_AUTHORIZED=1 in its
environment -- set by infra/terraform on the AgentCore Runtime and nowhere
else. A laptop that loads the agentcore profile still cannot call Bedrock.

The "model_gateway" branch is intentionally NotImplementedError, not a working
Model Gateway call: CLAUDE.md's "No paid model or cloud calls" and
"AWS/Bedrock/AgentCore adapters are contract-and-mock only until
explicitly authorized" both apply. Verified this session (not assumed):
strands-agents==1.55.1 dry-run installs cleanly on this machine's Python
3.14.6, matching the exact version verified on the bank's Artifactory
(docs/decision_record.md Tab 7) -- same pin both sides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


def default_model_id() -> str:
    """R20 (docs/refactor_plan.md §6h): the model id has ONE source -- the
    active profile's `llm.model` (config/profiles/<name>.yaml, selected by
    $DATAINSIGHTS_PROFILE, default fdm_local). Nothing else in the code
    base names a model; tests/test_model_id_single_source.py enforces it.
    Changing the model is a procedure, not a search-and-replace: see
    docs/model_change_procedure.md."""
    from datainsights.runtime import active_profile

    return active_profile().llm.model


def default_base_url() -> str:
    from datainsights.runtime import active_profile

    return active_profile().llm.base_url or "http://localhost:11434"


def model_id_for(task: str, profile_name: str | None = None) -> str:
    """The model this task should use: the profile's `llm.task_models[task]`
    if it names one, else `llm.model`.

    Different tasks need different capabilities, and the difference is
    measurable. A proposer must fill a structured-output schema whose
    result becomes pipeline configuration; a narrator writes prose that is
    validated afterwards in Python. A model can be fine at the second and
    unusable at the first -- qwen2.5:7b is exactly that, 0/4 on the
    proposer schema across two different candidates.

    Still R20-compliant: the tag lives in the profile, never in Python."""
    from datainsights.runtime import active_profile

    llm = active_profile(profile_name).llm
    return llm.task_models.get(task) or llm.model


@dataclass(frozen=True)
class ModelConfig:
    mode: Literal["local", "model_gateway", "bedrock"]
    model_id: str = field(default_factory=default_model_id)
    base_url: str = field(default_factory=default_base_url)
    temperature: float = 0.0
    region: str | None = None   # bedrock only


PAID_AWS_KEY = "DATAINSIGHTS_PAID_AWS_AUTHORIZED"


def get_model(cfg: ModelConfig):
    if cfg.mode == "local":
        from datainsights.config import LLMConfig

        # Raises if model_id ends in "-cloud" or base_url isn't localhost --
        # see datainsights/config.py's LLMConfig._enforce_local_only.
        LLMConfig(provider="ollama", base_url=cfg.base_url, model=cfg.model_id)

        from strands.models.ollama import OllamaModel

        return OllamaModel(host=cfg.base_url, model_id=cfg.model_id, temperature=cfg.temperature)

    if cfg.mode == "bedrock":
        if os.environ.get(PAID_AWS_KEY) != "1":
            raise PermissionError(
                f"mode='bedrock' refused: {PAID_AWS_KEY}=1 is not set. Bedrock is authorized only "
                "inside the AgentCore deployment (infra/terraform sets it there); never on a laptop, "
                "the dashboard, or CI. Use narrate=false for the deterministic template instead.")
        from strands.models.bedrock import BedrockModel

        guardrail = {}
        if os.environ.get("DATAINSIGHTS_BEDROCK_GUARDRAIL_ID"):
            # Terraform's Bedrock guardrail (infra/terraform/modules/agent_runtime):
            # masks client identifiers in anything the model reads or writes.
            guardrail = {"guardrail_id": os.environ["DATAINSIGHTS_BEDROCK_GUARDRAIL_ID"],
                         "guardrail_version": os.environ.get("DATAINSIGHTS_BEDROCK_GUARDRAIL_VERSION", "DRAFT")}
        return BedrockModel(model_id=cfg.model_id, region_name=cfg.region or os.environ.get("AWS_REGION"),
                            temperature=cfg.temperature, **guardrail)

    raise NotImplementedError(
        "mode='model_gateway' is contract-only, NOT RUN. Per CLAUDE.md: no "
        "paid model or cloud calls are authorized in this phase, and "
        "AWS/Bedrock/AgentCore adapters are contract-and-mock only until "
        "explicitly authorized. This branch documents the Stage 3 shape "
        "(docs/decision_record.md Tab 5) -- swap in a real internal Model "
        "Gateway client here only after that authorization and the "
        "5-phase AgentCore governance sign-off (Tab 5) actually exist."
    )
