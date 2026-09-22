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
    # "agentcore" validates and constructs (Phase 3, generalization_plan.md);
    # actually running under AgentCore Runtime is NOT RUN until deployed and
    # authorized -- this literal exists so a profile can be written and
    # tested for shape now, not so anything targets AWS today.
    target: Literal["local", "agentcore"] = "local"


class SourceConfig(BaseModel):
    # s3_parquet / glue_athena: construct and validate; the adapters raise
    # NotImplementedError with the exact blocking reason (see
    # datainsights/sources/s3_parquet_source.py, glue_athena_source.py) --
    # never a silent fallback to local data. See generalization_plan.md Phase 3.
    # "offline_local" -> FdmLocalSource (bi-temporal FDM-shaped contracts);
    # "offline_local_flat" -> OfflineLocalSource (flat, non-bi-temporal
    # contracts -- config/entities.yaml's legacy shape, config/entities_sba.yaml's
    # SBA-hybrid shape). Two backends, not one, because FdmLocalSource
    # unconditionally expects EFFECTIVE_START_DT/END_DT columns a flat
    # contract doesn't have.
    backend: Literal["offline_local", "offline_local_flat", "snowflake", "postgres", "sqlserver", "s3_parquet", "glue_athena"]
    entity_map_ref: str
    access: Literal["read_only"] = "read_only"
    # R14 (docs/refactor_plan.md §6a): was a free `str` nothing read. Now a
    # closed set, cross-checked against the backend below -- a cloud
    # backend claiming the local-free tier is a contradiction the loader
    # refuses, rather than a label nobody looks at.
    cost_policy: Literal["no_cost_local_files", "no_cost_until_deployed_not_run", "verified_trial_only"]
    # Which semantic binding (config/bindings/<name>.yaml) maps this source's
    # physical entities to the canonical model. None = legacy pipeline, which
    # predates the semantic model and reads entity_map_ref directly.
    # generalization_plan.md Phase 1.
    binding: Optional[str] = None

    # offline_local
    data_dir: Optional[str] = None

    # snowflake / postgres / sqlserver / glue_athena (R5: one SqlSource, dialect-aware)
    connection_ref: Optional[str] = None
    # contract `domain:` -> physical schema (e.g. {"kernel": "ENT_PRD.TIER0_PRS"}); a flat
    # contract with no domains needs only `default_schema`
    domain_schema_map: dict[str, str] = {}
    default_schema: Optional[str] = None
    warehouse_size: Optional[str] = None
    auto_suspend_seconds: Optional[int] = None
    query_timeout_seconds: Optional[int] = None
    statement_row_limit: Optional[int] = None

    # s3_parquet
    s3_uri: Optional[str] = None
    # glue_athena
    glue_database: Optional[str] = None
    athena_workgroup: Optional[str] = None

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "SourceConfig":
        if self.backend == "offline_local" and not self.data_dir:
            raise ValueError("offline_local source requires data_dir")
        if self.backend in ("snowflake", "postgres", "sqlserver") and not self.connection_ref:
            raise ValueError(f"{self.backend} source requires connection_ref")
        if self.backend == "s3_parquet" and not self.s3_uri:
            raise ValueError("s3_parquet source requires s3_uri (s3://... or file://... for local proof)")
        if self.backend == "glue_athena" and not self.glue_database:
            raise ValueError("glue_athena source requires glue_database")
        cloud = self.backend in ("snowflake", "postgres", "sqlserver", "s3_parquet", "glue_athena")
        if cloud and self.cost_policy == "no_cost_local_files":
            raise ValueError(
                f"backend={self.backend!r} cannot declare cost_policy='no_cost_local_files' -- "
                f"a cloud backend is never local-free. Use 'no_cost_until_deployed_not_run' "
                f"(contract-only, nothing runs) or 'verified_trial_only'."
            )
        if not cloud and self.cost_policy != "no_cost_local_files":
            raise ValueError(f"backend={self.backend!r} is local; cost_policy must be 'no_cost_local_files'")
        return self


class EventSourceConfig(BaseModel):
    """Where exogenous events come from -- generalization_plan.md Phase 2.
    Optional on every profile; a profile with no events block simply runs
    with no exogenous checks (today's legacy-pipeline behaviour)."""
    backend: Literal["csv", "s3", "snowflake"] = "csv"
    path: Optional[str] = None       # csv
    s3_uri: Optional[str] = None     # s3 (NOT RUN until Phase 3)
    connection_ref: Optional[str] = None  # snowflake (NOT RUN until Phase 3)

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "EventSourceConfig":
        if self.backend == "csv" and not self.path:
            raise ValueError("csv event source requires path")
        if self.backend == "s3" and not self.s3_uri:
            raise ValueError("s3 event source requires s3_uri")
        if self.backend == "snowflake" and not self.connection_ref:
            raise ValueError("snowflake event source requires connection_ref")
        return self


class AnalyticsConfig(BaseModel):
    backend: Literal["duckdb_local"] = "duckdb_local"


class LLMConfig(BaseModel):
    # "model_gateway": constructs and validates; agents/model_factory.get_model()
    # raises NotImplementedError for it until Stage 3 is authorized (D4,
    # docs/decision_record.md) -- never silently routes to a real gateway.
    provider: Literal["ollama", "model_gateway"]
    base_url: Optional[str] = None
    model: str
    allow_remote_inference: bool = False
    allow_paid_fallback: bool = False
    # R14: was a free str nothing read. The deterministic template is the
    # ONLY fallback that exists and the only one CLAUDE.md permits ("never to a
    # paid provider") -- a one-value Literal makes any other fallback
    # unconfigurable, not merely unimplemented.
    fallback: Literal["deterministic_template"] = "deterministic_template"
    # Per-task model overrides, e.g. {"proposer": "llama3.1:8b"}. `model`
    # above stays the default for every task that has no entry here.
    #
    # Why this exists, measured rather than assumed: qwen2.5:7b -- the
    # default -- reasons correctly in prose but fails to invoke a
    # structured-output tool. Probed four times on each of two different
    # candidates, it produced usable output 0/4 both times, while
    # llama3.1:8b managed 4/4 and was faster. A proposer whose output
    # becomes pipeline configuration needs a model that can actually fill
    # the schema; a narrator does not.
    #
    # This does NOT weaken R20 (tests/test_model_id_single_source.py): the
    # rule is that no PYTHON file names a model tag. Profiles are where a
    # tag legitimately lives, and this keeps every tag in the profile.
    task_models: dict[str, str] = Field(default_factory=dict)

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
        # Every tag, default AND per-task: an override must not become a
        # way around the cost policy.
        for label, tag in [("model", self.model)] + sorted(self.task_models.items()):
            if tag.endswith("-cloud"):
                raise ValueError(
                    f"{label} '{tag}' is an Ollama cloud-routed tag, not local "
                    "inference -- not permitted under this project's cost policy"
                )
        if self.provider == "ollama":
            if not self.base_url:
                raise ValueError("ollama provider requires base_url")
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
    # "dynamodb": constructs; datainsights.state_store.DynamoDBStateStore
    # raises NotImplementedError until Phase 3 is run for real. See
    # generalization_plan.md.
    backend: Literal["sqlite", "dynamodb"] = "sqlite"
    path: Optional[str] = None
    table_name: Optional[str] = None

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "StateConfig":
        if self.backend == "sqlite" and not self.path:
            raise ValueError("sqlite state requires path")
        if self.backend == "dynamodb" and not self.table_name:
            raise ValueError("dynamodb state requires table_name")
        return self


class OutputConfig(BaseModel):
    backend: Literal["local", "s3"] = "local"
    path: Optional[str] = None
    s3_uri: Optional[str] = None

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "OutputConfig":
        if self.backend == "local" and not self.path:
            raise ValueError("local output requires path")
        if self.backend == "s3" and not self.s3_uri:
            raise ValueError("s3 output requires s3_uri")
        return self


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


class IdentityConfig(BaseModel):
    """R21 (docs/refactor_plan.md §6j): who is looking. `local_dev` is a
    developer identity declared here (or overridden by DATAINSIGHTS_USER /
    DATAINSIGHTS_ROLE / DATAINSIGHTS_RM_IDS) -- honest about being a dev
    stand-in. `idp` is the contract for the bank's identity provider at
    Stage 3: declared, NOT RUN, raises if selected. The worklist is scoped
    server-side from the resolved principal (datainsights/identity.py);
    the UI never offers a way to pick another RM."""
    provider: Literal["local_dev", "idp"] = "local_dev"
    user: str = "local_dev_user"
    role: Literal["rm", "supervisor", "admin"] = "rm"
    rm_ids: list[str] = []


class Profile(BaseModel):
    # R14: was declared and checked by nothing. A version field that is
    # never compared is not a version field.
    config_version: Literal[1]
    profile: str
    runtime: RuntimeConfig
    source: SourceConfig
    analytics: AnalyticsConfig
    llm: LLMConfig
    judge: Optional[JudgeConfig] = None
    event_source: Optional[EventSourceConfig] = None
    state: StateConfig
    output: OutputConfig
    monitor: MonitorConfig
    cost: CostConfig
    identity: IdentityConfig = IdentityConfig()


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
    'fdm_local' if neither is set -- a local-files default, a
    Snowflake/paid-capable profile is never selected implicitly)."""
    name = name or os.environ.get("DATAINSIGHTS_PROFILE", "fdm_local")
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
