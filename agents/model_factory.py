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

The non-local branch is intentionally NotImplementedError, not a working
Bedrock/Model Gateway call: CLAUDE.md's "No paid model or cloud calls" and
"AWS/Bedrock/AgentCore adapters are contract-and-mock only until
explicitly authorized" both apply. Verified this session (not assumed):
strands-agents==1.55.1 dry-run installs cleanly on this machine's Python
3.14.6, matching the exact version verified on the bank's Artifactory
(docs/decision_record.md Tab 7) -- same pin both sides.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class ModelConfig:
    mode: Literal["local", "model_gateway"]
    model_id: str = field(default_factory=default_model_id)
    base_url: str = field(default_factory=default_base_url)
    temperature: float = 0.0


def get_model(cfg: ModelConfig):
    if cfg.mode == "local":
        from datainsights.config import LLMConfig

        # Raises if model_id ends in "-cloud" or base_url isn't localhost --
        # see datainsights/config.py's LLMConfig._enforce_local_only.
        LLMConfig(provider="ollama", base_url=cfg.base_url, model=cfg.model_id)

        from strands.models.ollama import OllamaModel

        return OllamaModel(host=cfg.base_url, model_id=cfg.model_id, temperature=cfg.temperature)

    raise NotImplementedError(
        "mode='model_gateway' is contract-only, NOT RUN. Per CLAUDE.md: no "
        "paid model or cloud calls are authorized in this phase, and "
        "AWS/Bedrock/AgentCore adapters are contract-and-mock only until "
        "explicitly authorized. This branch documents the Stage 3 shape "
        "(docs/decision_record.md Tab 5) -- swap in a real internal Model "
        "Gateway client here only after that authorization and the "
        "5-phase AgentCore governance sign-off (Tab 5) actually exist."
    )
