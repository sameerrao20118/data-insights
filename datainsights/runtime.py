"""
build_runtime(): the single composition point for the FDM-aligned build
(docs/generalization_plan.md Phase 0). Every entry point that used to
construct `FdmLocalSource(FDM_DIR, CONTRACT_PATH)` by hand now calls
this instead, reading `datainsights.config.Profile` the same way the
legacy pipeline already does.

This does NOT change what runs today -- `fdm_local` profile reproduces
the exact hard-coded defaults every demo/script used before this existed
(verified: `tests/test_runtime.py`'s regression assertion). What it adds
is one place cloud backends (s3_parquet, glue_athena, dynamodb state,
model_gateway) get selected from, so Phase 3 fills in real adapters
without touching any of these entry points again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from agents.model_factory import ModelConfig
from datainsights.config import Profile, load_profile
from datainsights.sources.base import DataSource

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Runtime:
    profile: Profile
    source: DataSource
    rules: dict
    model_config: ModelConfig
    event_source_path: str | None   # csv path today; None if the profile has no event_source
    state_db_path: str
    out_dir: str
    contract_path: str
    binding_name: str | None        # config/bindings/<name>.yaml -- None until Phase 1 lands


def _build_source(profile: Profile) -> DataSource:
    backend = profile.source.backend
    contract_path = str(REPO_ROOT / profile.source.entity_map_ref)

    if backend == "offline_local":
        from datainsights.sources.fdm_local import FdmLocalSource

        data_dir = profile.source.data_dir
        if not os.path.isabs(data_dir):
            data_dir = str(REPO_ROOT / data_dir)
        return FdmLocalSource(data_dir, contract_path)

    if backend == "snowflake":
        # NOT RUN -- no credentials, per CLAUDE.md. FdmSnowflakeSource also
        # requires a domain_schema_map (which ENT_PRD schema each
        # config/entities_fdm.yaml `domain:` maps to) that no profile field
        # carries yet; add one when a real Snowflake profile is authorized
        # rather than guess a mapping now.
        raise NotImplementedError(
            "source.backend='snowflake' needs a domain_schema_map (see "
            "datainsights.sources.fdm_snowflake.FdmSnowflakeSource) not yet "
            "exposed on a Profile, plus real credentials. NOT RUN -- construct "
            "FdmSnowflakeSource directly if you have both, per CLAUDE.md's "
            "explicit-authorization requirement."
        )

    if backend == "s3_parquet":
        raise NotImplementedError(
            "source.backend='s3_parquet' is contract-only (docs/generalization_plan.md "
            "Phase 3) -- datainsights/sources/s3_parquet_source.py does not exist yet. "
            "Build it there before selecting this backend."
        )

    if backend == "glue_athena":
        raise NotImplementedError(
            "source.backend='glue_athena' is contract-only (docs/generalization_plan.md "
            "Phase 3, Slot A2/A4) -- no confirmed source and no adapter exists yet. "
            "Needs AWS credentials, a Glue catalog, and explicit authorization per CLAUDE.md."
        )

    raise ValueError(f"unknown source backend: {backend!r}")


def build_runtime(profile_name: str | None = None, *, data_dir_override: str | None = None,
                   events_path_override: str | None = None) -> Runtime:
    """profile_name defaults to $DATAINSIGHTS_PROFILE or 'fdm_local'.
    The two overrides exist ONLY for the ML scale-comparison scripts
    (datainsights/ml/compare_baselines.py, scale_evaluation.py), which
    need to point at data_generator/output_fdm_scaled* without a profile
    file per dataset -- everything else should use a named profile."""
    profile_name = profile_name or os.environ.get("DATAINSIGHTS_PROFILE", "fdm_local")
    profile = load_profile(profile_name) if _is_legacy_profile(profile_name) else _load_fdm_profile(profile_name)

    # profile.runtime.target == "agentcore" constructs fully here (proves the
    # profile is valid) -- nothing about AgentCore Runtime itself is invoked
    # anywhere in this repo. See docs/generalization_plan.md Phase 3.

    if data_dir_override:
        profile.source.data_dir = data_dir_override

    source = _build_source(profile)

    rules_path = REPO_ROOT / "config" / "rules.yaml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    model_config = ModelConfig(
        mode="local" if profile.llm.provider == "ollama" else "model_gateway",
        model_id=profile.llm.model,
        base_url=profile.llm.base_url or "http://localhost:11434",
    )

    event_source_path = None
    if events_path_override:
        event_source_path = events_path_override
    elif profile.event_source and profile.event_source.backend == "csv":
        p = profile.event_source.path
        event_source_path = p if os.path.isabs(p) else str(REPO_ROOT / p)
    elif profile.event_source:
        raise NotImplementedError(
            f"event_source.backend={profile.event_source.backend!r} is contract-only "
            "(docs/generalization_plan.md Phase 3) -- only 'csv' is implemented."
        )

    state_path = profile.state.path or "var/agent_traces.db"
    if profile.state.backend == "dynamodb":
        raise NotImplementedError(
            "state.backend='dynamodb' is contract-only (docs/generalization_plan.md Phase 3)."
        )
    out_dir = profile.output.path or "var/insights"
    if profile.output.backend == "s3":
        raise NotImplementedError(
            "output.backend='s3' is contract-only (docs/generalization_plan.md Phase 3)."
        )

    return Runtime(
        profile=profile,
        source=source,
        rules=rules,
        model_config=model_config,
        event_source_path=event_source_path,
        state_db_path=str(REPO_ROOT / state_path) if not os.path.isabs(state_path) else state_path,
        out_dir=str(REPO_ROOT / out_dir) if not os.path.isabs(out_dir) else out_dir,
        contract_path=str(REPO_ROOT / profile.source.entity_map_ref),
        binding_name=profile.source.binding,
    )


def _is_legacy_profile(name: str) -> bool:
    return name in ("offline_ollama", "snowflake_trial_ollama")


def _load_fdm_profile(name: str) -> Profile:
    path = REPO_ROOT / "config" / "profiles" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No profile at {path}. FDM profiles live in config/profiles/ alongside the "
            f"legacy ones -- see config/profiles/fdm_local.yaml."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Profile.model_validate(raw)
