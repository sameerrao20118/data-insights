"""
Typed, validated profile configuration. Small interfaces, not a framework:
this module only loads and validates config -- it does not itself connect
to anything. See project instructions section 4.1.

Placeholder/unset values must fail validation rather than silently
defaulting to something that looks like it works. In particular:
  - a snowflake-backed profile with any required env var unset raises,
    it does not fall back to offline_local silently.
  - paid_llm_calls_allowed / paid_cloud_services_allowed are hard-blocked
    at the code level, not just declared false in YAML -- flipping them in
    a profile file alone does not enable paid calls.
  - an Ollama model tag ending in "-cloud" is rejected: those route to
    Ollama's cloud service, not local inference, and this project's cost
    policy requires local-only inference in this phase.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "config" / "profiles"


class RuntimeConfig(BaseModel):
    target: Literal["local"] = "local"


class SourceConfig(BaseModel):
    backend: Literal["offline_local", "snowflake"]
    entity_map_ref: str
    access: Literal["read_only"] = "read_only"
    cost_policy: str

    # offline_local
    data_dir: Optional[str] = None

    # snowflake
    connection_ref: Optional[str] = None
    warehouse_size: Optional[str] = None
    auto_suspend_seconds: Optional[int] = None
    query_timeout_seconds: Optional[int] = None
    statement_row_limit: Optional[int] = None

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "SourceConfig":
        if self.backend == "offline_local" and not self.data_dir:
            raise ValueError("offline_local source requires data_dir")
        if self.backend == "snowflake" and not self.connection_ref:
            raise ValueError("snowflake source requires connection_ref")
        return self


class AnalyticsConfig(BaseModel):
    backend: Literal["duckdb_local"] = "duckdb_local"


class LLMConfig(BaseModel):
    provider: Literal["ollama"]
    base_url: str
    model: str
    allow_remote_inference: bool = False
    allow_paid_fallback: bool = False
    fallback: str = "deterministic_template"

    @model_validator(mode="after")
    def _enforce_local_only(self) -> "LLMConfig":
        if self.allow_remote_inference:
            raise ValueError(
                "allow_remote_inference=true is not authorized in this phase"
            )
        if self.allow_paid_fallback:
            raise ValueError(
                "allow_paid_fallback=true is not authorized in this phase"
            )
        if self.model.endswith("-cloud"):
            raise ValueError(
                f"model '{self.model}' is an Ollama cloud-routed tag, not local "
                "inference -- not permitted under this project's cost policy"
            )
        if not self.base_url.startswith("http://127.0.0.1") and not self.base_url.startswith("http://localhost"):
            raise ValueError(
                f"base_url '{self.base_url}' is not localhost -- refusing a "
                "non-local Ollama endpoint under this project's cost policy"
            )
        return self


class JudgeConfig(BaseModel):
    provider: Literal["ollama"]
    model: str
    mode: Literal["offline_sampled"] = "offline_sampled"


class StateConfig(BaseModel):
    backend: Literal["sqlite"] = "sqlite"
    path: str


class OutputConfig(BaseModel):
    backend: Literal["local"] = "local"
    path: str


class MonitorConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int = 60
    max_concurrent_runs: int = 1
    lookback_ref: Optional[str] = None


class CostConfig(BaseModel):
    paid_llm_calls_allowed: bool = False
    paid_cloud_services_allowed: bool = False

    @model_validator(mode="after")
    def _hard_block_paid(self) -> "CostConfig":
        # Defense in depth: even if someone edits a profile YAML to flip
        # these, the loader still refuses. This is a POC-wide constraint,
        # not something a config file alone should be able to lift.
        if self.paid_llm_calls_allowed or self.paid_cloud_services_allowed:
            raise ValueError(
                "paid_llm_calls_allowed / paid_cloud_services_allowed must be "
                "false -- no paid model or cloud service calls are authorized "
                "in this project phase"
            )
        return self


class Profile(BaseModel):
    config_version: int
    profile: str
    runtime: RuntimeConfig
    source: SourceConfig
    analytics: AnalyticsConfig
    llm: LLMConfig
    judge: JudgeConfig
    state: StateConfig
    output: OutputConfig
    monitor: MonitorConfig
    cost: CostConfig


class SnowflakeConnectionParams(BaseModel):
    """Resolved from env vars named <PREFIX>_<FIELD>, PREFIX = connection_ref
    upper-cased. Never populated from a YAML file. Values are held in memory
    only; callers must not log or print this object (repr is redacted)."""

    account: str
    user: str
    password: str
    warehouse: str
    database: str
    schema_: str = Field(alias="schema")
    role: Optional[str] = None

    def __repr__(self) -> str:
        return f"SnowflakeConnectionParams(account={self.account!r}, user={self.user!r}, <redacted>)"

    __str__ = __repr__


def load_profile(name: Optional[str] = None) -> Profile:
    """Load and validate a profile by name (or $DATAINSIGHTS_PROFILE, or
    'offline_ollama' if neither is set -- offline is the safe default, a
    Snowflake/paid-capable profile is never selected implicitly)."""
    name = name or os.environ.get("DATAINSIGHTS_PROFILE", "offline_ollama")
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No profile file at {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Profile.model_validate(raw)


def resolve_snowflake_connection(connection_ref: str) -> SnowflakeConnectionParams:
    """Read SNOWFLAKE_<REF>_* env vars. Raises with a clear, non-secret-
    leaking message naming which var is missing, rather than silently
    falling back to anything. Fail closed, per project instructions."""
    prefix = f"SNOWFLAKE_{connection_ref.upper()}_"
    required = ["ACCOUNT", "USER", "PASSWORD", "WAREHOUSE", "DATABASE", "SCHEMA"]
    missing = [f"{prefix}{field}" for field in required if not os.environ.get(f"{prefix}{field}")]
    if missing:
        raise EnvironmentError(
            f"Snowflake connection '{connection_ref}' is not configured. "
            f"Missing environment variables: {', '.join(missing)}. "
            "Set them in your own shell profile / .env (gitignored) -- "
            "never in a config file, prompt, or log."
        )
    return SnowflakeConnectionParams(
        account=os.environ[f"{prefix}ACCOUNT"],
        user=os.environ[f"{prefix}USER"],
        password=os.environ[f"{prefix}PASSWORD"],
        warehouse=os.environ[f"{prefix}WAREHOUSE"],
        database=os.environ[f"{prefix}DATABASE"],
        schema=os.environ[f"{prefix}SCHEMA"],
        role=os.environ.get(f"{prefix}ROLE"),
    )
