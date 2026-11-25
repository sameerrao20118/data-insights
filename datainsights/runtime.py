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
import re
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
    # AWS (datainsights/aws_backends.py): set only when the profile selects
    # output.backend=s3 / state.backend=dynamodb. out_dir/state_db_path are
    # then an ephemeral scratch directory the session publishes from.
    output_s3_uri: str | None = None
    ledger_table: str | None = None


def _build_source(profile: Profile) -> DataSource:
    backend = profile.source.backend
    contract_path = str(REPO_ROOT / profile.source.entity_map_ref)

    if backend == "offline_local":
        from datainsights.sources.fdm_local import FdmLocalSource

        data_dir = profile.source.data_dir
        if not os.path.isabs(data_dir):
            data_dir = str(REPO_ROOT / data_dir)
        return FdmLocalSource(data_dir, contract_path)

    if backend == "offline_local_flat":
        from datainsights.sources.offline_local import OfflineLocalSource

        data_dir = profile.source.data_dir
        if not os.path.isabs(data_dir):
            data_dir = str(REPO_ROOT / data_dir)
        return OfflineLocalSource(data_dir, contract_path)

    if backend in ("snowflake", "postgres", "sqlserver"):
        # R5: one dialect-aware SqlSource. Constructing never connects; the
        # first read needs <CONNECTION_REF>_* env vars and the driver, and
        # raises a plain error naming what is missing. NOT RUN in this repo.
        from datainsights.sources.sql_source import SqlSource

        return SqlSource(
            backend, contract_path, connection_ref=profile.source.connection_ref,
            domain_schema_map=profile.source.domain_schema_map, default_schema=profile.source.default_schema,
            statement_row_limit=profile.source.statement_row_limit or 50_000,
            query_timeout_seconds=profile.source.query_timeout_seconds or 30,
            warehouse_size=profile.source.warehouse_size, auto_suspend_seconds=profile.source.auto_suspend_seconds,
        )

    if backend == "s3_parquet":
        raise NotImplementedError(
            "source.backend='s3_parquet' is contract-only (docs/generalization_plan.md "
            "Phase 3) -- datainsights/sources/s3_parquet_source.py does not exist yet. "
            "Build it there before selecting this backend."
        )

    if backend == "glue_athena":
        from datainsights.sources.sql_source import AthenaSource

        return AthenaSource(
            contract_path, glue_database=profile.source.glue_database, connection_ref=profile.source.connection_ref,
            athena_workgroup=profile.source.athena_workgroup, domain_schema_map=profile.source.domain_schema_map,
            statement_row_limit=profile.source.statement_row_limit or 50_000,
            query_timeout_seconds=profile.source.query_timeout_seconds or 30,
        )

    raise ValueError(f"unknown source backend: {backend!r}")


def build_runtime(profile_name: str | None = None, *, data_dir_override: str | None = None,
                   events_path_override: str | None = None, cache: bool = True) -> Runtime:
    """profile_name defaults to $DATAINSIGHTS_PROFILE or 'fdm_local'.
    The two overrides exist ONLY for the ML scale-comparison scripts
    (datainsights/ml/compare_baselines.py, scale_evaluation.py), which
    need to point at data_generator/output_fdm_scaled* without a profile
    file per dataset -- everything else should use a named profile."""
    profile = active_profile(profile_name)

    # profile.runtime.target == "agentcore" constructs fully here (proves the
    # profile is valid) -- nothing about AgentCore Runtime itself is invoked
    # anywhere in this repo. See docs/generalization_plan.md Phase 3.

    if data_dir_override:
        profile.source.data_dir = data_dir_override

    source = _build_source(profile)
    if cache:
        # Measured, not assumed: without this the whole-book path re-reads
        # every physical table once PER CLIENT -- 300 clients over a
        # 182k-row dataset materialised 98,063,596 rows in 139s. Wrapping
        # here makes it 181,848 rows in 19s, and flattens ms/client so it
        # stops growing with book size. RunScopedCache has been built and
        # tested since M8 but was never wired into a real run path.
        #
        # Scope is ONE run at ONE as-of (see RunScopedCache's docstring):
        # build_runtime() is called once per run by every entry point, so
        # that holds. Pass cache=False for a long-lived process that
        # sweeps multiple as-of dates through a single Runtime.
        from datainsights.sources.caching import RunScopedCache

        source = RunScopedCache(source)

    rules_path = REPO_ROOT / "config" / "rules.yaml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)
    # R6: a binding may carry `rules:` overrides (config/bindings/<schema>.yaml)
    # -- merged over the global defaults, per leaf, so one schema's
    # thresholds never touch another's.
    if profile.source.binding:
        from datainsights.semantic.binding import load_binding, merge_rules

        rules = merge_rules(rules, load_binding(profile.source.binding).rules)

    model_config = ModelConfig(
        mode={"ollama": "local", "bedrock": "bedrock"}.get(profile.llm.provider, "model_gateway"),
        model_id=profile.llm.model,
        base_url=profile.llm.base_url or "http://localhost:11434",
        region=profile.llm.region,
    )

    event_source_path = None
    if events_path_override:
        event_source_path = events_path_override
    elif profile.event_source and profile.event_source.backend == "csv":
        p = profile.event_source.path
        event_source_path = p if os.path.isabs(p) else str(REPO_ROOT / p)
    elif profile.event_source and profile.event_source.backend == "s3":
        from datainsights.aws_backends import fetch_s3_object, scratch_dir

        event_source_path = fetch_s3_object(profile.event_source.s3_uri, scratch_dir("events"))
    elif profile.event_source:
        raise NotImplementedError(
            f"event_source.backend={profile.event_source.backend!r} is contract-only "
            "(docs/generalization_plan.md Phase 3) -- only 'csv' and 's3' are implemented."
        )

    # dynamodb state / s3 output (docs/aws_deployment.md): the pipeline keeps
    # writing sqlite + files locally -- to session scratch -- and
    # datainsights/aws_backends.py moves them to DynamoDB/S3.
    state_path = profile.state.path or "var/agent_traces.db"
    ledger_table = None
    if profile.state.backend == "dynamodb":
        from datainsights.aws_backends import scratch_dir

        state_path = os.path.join(scratch_dir("traces"), "agent_traces.db")
        ledger_table = profile.state.table_name
    out_dir = profile.output.path or "var/insights"
    output_s3_uri = None
    if profile.output.backend == "s3":
        from datainsights.aws_backends import scratch_dir

        out_dir = scratch_dir("insights")
        output_s3_uri = profile.output.s3_uri

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
        output_s3_uri=output_s3_uri,
        ledger_table=ledger_table,
    )


def active_profile(profile_name: str | None = None) -> Profile:
    """The one place a profile is resolved: the given name, else
    $DATAINSIGHTS_PROFILE, else fdm_local. R20: agents/model_factory.py
    reads the model id from here and nowhere else."""
    name = profile_name or os.environ.get("DATAINSIGHTS_PROFILE", "fdm_local")
    return load_profile(name) if _is_legacy_profile(name) else _load_fdm_profile(name)


def _is_legacy_profile(name: str) -> bool:
    return name == "snowflake_trial_ollama"  # R23: offline_ollama (legacy) is gone


def _load_fdm_profile(name: str) -> Profile:
    path = REPO_ROOT / "config" / "profiles" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No profile at {path}. FDM profiles live in config/profiles/ alongside the "
            f"legacy ones -- see config/profiles/fdm_local.yaml."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Profile.model_validate(_expand_env(raw, name))


_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def _expand_env(node, profile_name: str):
    """`${VAR}` in a profile value is read from the environment -- how a
    deployed profile names a bucket, table or model id that only exists
    once Terraform has created it (config/profiles/fdm_agentcore.yaml). An
    unset variable is an error naming it: never an empty string, never a
    default that looks like it works."""
    if isinstance(node, dict):
        return {k: _expand_env(v, profile_name) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env(v, profile_name) for v in node]
    if isinstance(node, str):
        missing = [v for v in _ENV_REF.findall(node) if not os.environ.get(v)]
        if missing:
            raise EnvironmentError(
                f"profile {profile_name!r} needs environment variable(s) {', '.join(missing)} -- on AWS "
                f"infra/terraform sets them on the AgentCore Runtime; locally, export them yourself.")
        return _ENV_REF.sub(lambda m: os.environ[m.group(1)], node)
    return node
