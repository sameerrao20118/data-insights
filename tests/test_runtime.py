"""
Phase 0 verification (docs/generalization_plan.md): build_runtime()
composes source/rules/model/event-source/state/output from a profile;
cloud backends construct-and-raise with a named reason rather than
silently falling back to local data; every existing config validator
still fires.
"""

import os

import pytest

from datainsights.config import EventSourceConfig, OutputConfig, Profile, RuntimeConfig, SourceConfig, StateConfig
from datainsights.runtime import build_runtime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


def test_fdm_local_profile_builds_a_working_runtime():
    rt = build_runtime("fdm_local")
    assert rt.source.capabilities().backend == "fdm_local"
    assert rt.model_config.mode == "local"
    assert rt.event_source_path and rt.event_source_path.endswith("tender_events.csv")
    assert rt.binding_name == "fdm"
    assert "fdm_rule_version" in rt.rules


def test_s3_parquet_backend_raises_not_implemented_not_silently_local():
    profile = Profile(
        config_version=1, profile="test_s3",
        runtime=RuntimeConfig(target="local"),
        source=SourceConfig(backend="s3_parquet", entity_map_ref="config/entities_fdm.yaml",
                            cost_policy="x", s3_uri="s3://bucket/prefix"),
        analytics={"backend": "duckdb_local"},
        llm={"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"},
        state=StateConfig(backend="sqlite", path="var/x.db"),
        output=OutputConfig(backend="local", path="var/insights"),
        monitor={},
        cost={},
    )
    import datainsights.runtime as rtmod
    with pytest.raises(NotImplementedError, match="s3_parquet"):
        rtmod._build_source(profile)


def test_glue_athena_backend_raises_not_implemented():
    profile = Profile(
        config_version=1, profile="test_glue",
        runtime=RuntimeConfig(target="local"),
        source=SourceConfig(backend="glue_athena", entity_map_ref="config/entities_fdm.yaml",
                            cost_policy="x", glue_database="db"),
        analytics={"backend": "duckdb_local"},
        llm={"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"},
        state=StateConfig(backend="sqlite", path="var/x.db"),
        output=OutputConfig(backend="local", path="var/insights"),
        monitor={},
        cost={},
    )
    import datainsights.runtime as rtmod
    with pytest.raises(NotImplementedError, match="glue_athena"):
        rtmod._build_source(profile)


def test_model_gateway_provider_validates_but_get_model_raises():
    from agents.model_factory import ModelConfig, get_model

    profile = Profile(
        config_version=1, profile="test_gateway",
        runtime=RuntimeConfig(target="agentcore"),
        source=SourceConfig(backend="offline_local", entity_map_ref="config/entities_fdm.yaml",
                            cost_policy="x", data_dir="data_generator/output_fdm"),
        analytics={"backend": "duckdb_local"},
        llm={"provider": "model_gateway", "model": "some-internal-model"},
        state=StateConfig(backend="sqlite", path="var/x.db"),
        output=OutputConfig(backend="local", path="var/insights"),
        monitor={},
        cost={},
    )
    assert profile.llm.provider == "model_gateway"  # constructs fine -- LLMConfig doesn't reject this provider
    with pytest.raises(NotImplementedError, match="model_gateway"):
        get_model(ModelConfig(mode="model_gateway", model_id=profile.llm.model))


def _base_profile(**overrides) -> Profile:
    fields = dict(
        config_version=1, profile="test", runtime=RuntimeConfig(target="local"),
        source=SourceConfig(backend="offline_local", entity_map_ref="config/entities_fdm.yaml",
                            cost_policy="x", data_dir="data_generator/output_fdm"),
        analytics={"backend": "duckdb_local"},
        llm={"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"},
        state=StateConfig(backend="sqlite", path="var/x.db"),
        output=OutputConfig(backend="local", path="var/insights"),
        monitor={}, cost={},
    )
    fields.update(overrides)
    return Profile(**fields)


def test_dynamodb_state_backend_validates_but_build_runtime_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("datainsights.runtime._load_fdm_profile",
                        lambda name: _base_profile(state=StateConfig(backend="dynamodb", table_name="x")))
    with pytest.raises(NotImplementedError, match="dynamodb"):
        build_runtime("test")


def test_s3_output_backend_validates_but_build_runtime_raises(monkeypatch):
    monkeypatch.setattr("datainsights.runtime._load_fdm_profile",
                        lambda name: _base_profile(output=OutputConfig(backend="s3", s3_uri="s3://x")))
    with pytest.raises(NotImplementedError, match="s3"):
        build_runtime("test")


def test_event_source_backend_other_than_csv_validates_but_build_runtime_raises(monkeypatch):
    monkeypatch.setattr(
        "datainsights.runtime._load_fdm_profile",
        lambda name: _base_profile(event_source=EventSourceConfig(backend="s3", s3_uri="s3://x")))
    with pytest.raises(NotImplementedError, match="s3"):
        build_runtime("test")


def test_regression_oracle_matches_direct_construction():
    """The actual Phase 0 acceptance: build_runtime's FdmLocalSource reads
    the identical data every hand-wired script used to construct directly."""
    from datainsights.sources.fdm_local import FdmLocalSource

    rt = build_runtime("fdm_local")
    direct = FdmLocalSource(FDM_DIR, os.path.join(REPO_ROOT, "config", "entities_fdm.yaml"))
    from datetime import date
    assert rt.source.party(date(2025, 10, 4)).equals(direct.party(date(2025, 10, 4)))
