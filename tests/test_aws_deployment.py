"""
The AWS deployment's code paths (docs/aws_deployment.md), with no AWS:
fakes stand in for S3 and DynamoDB, and Bedrock is only constructed,
never called.

What these guard:
  * Bedrock is reachable ONLY from an agentcore profile carrying a dated
    paid_authorization AND a process with DATAINSIGHTS_PAID_AWS_AUTHORIZED=1;
    every local profile stays hard-blocked.
  * the fully-wired fdm_agentcore profile builds an Athena source, a Bedrock
    model config, an S3 event feed, a DynamoDB ledger and S3 output.
  * a book run publishes its files under <as_of>/<run_id>/ and latest/,
    records the run, and holds a cross-session lock.
  * Athena parameters are named and dates are DATE literals.
"""

from __future__ import annotations

import os
import shutil
from datetime import date

import pytest

from agents.model_factory import PAID_AWS_KEY, ModelConfig, get_model
from datainsights.aws_backends import RunLedger, RunOverlapError, fetch_s3_object, parse_s3_uri, publish_files
from datainsights.config import CostConfig, Profile
from datainsights.sources.sql_source import _athena_params

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

AUTH = {"scope": "aws_agentcore_bedrock", "authorized_by": "t", "authorized_on": "2026-10-23", "reference": "x"}
DEPLOY_ENV = {
    "DATAINSIGHTS_GLUE_DB_KERNEL": "di_kernel", "DATAINSIGHTS_GLUE_DB_LENDING": "di_lending",
    "DATAINSIGHTS_ATHENA_WORKGROUP": "di_agents", "DATAINSIGHTS_BEDROCK_MODEL_ID": "test.model-id",
    "AWS_REGION": "eu-west-2", "DATAINSIGHTS_EVENTS_S3_URI": "s3://events-bucket/raw/tender_events.csv",
    "DATAINSIGHTS_LEDGER_TABLE": "di-ledger", "DATAINSIGHTS_INSIGHTS_S3_URI": "s3://insights-bucket/insights",
}


def _profile(**kw) -> dict:
    base = dict(
        config_version=1, profile="t", runtime={"target": "agentcore"},
        source={"backend": "offline_local", "entity_map_ref": "config/entities_fdm.yaml",
                "cost_policy": "no_cost_local_files", "data_dir": "data_generator/output_fdm"},
        analytics={"backend": "duckdb_local"},
        llm={"provider": "bedrock", "model": "m", "allow_remote_inference": True},
        state={"backend": "sqlite", "path": "var/x.db"}, output={"backend": "local", "path": "var/i"},
        monitor={}, cost={"paid_llm_calls_allowed": True, "paid_authorization": AUTH},
    )
    base.update(kw)
    return base


# --- the paid gate ---------------------------------------------------------

def test_bedrock_validates_on_an_authorized_agentcore_profile():
    assert Profile.model_validate(_profile()).llm.provider == "bedrock"


def test_bedrock_is_refused_on_a_local_profile_even_with_an_authorization():
    with pytest.raises(ValueError, match="agentcore"):
        Profile.model_validate(_profile(runtime={"target": "local"}))


def test_bedrock_is_refused_without_a_paid_authorization():
    with pytest.raises(ValueError):
        Profile.model_validate(_profile(cost={"paid_llm_calls_allowed": True}))
    with pytest.raises(ValueError, match="paid_authorization"):
        Profile.model_validate(_profile(cost={}))


def test_bedrock_must_declare_itself_remote_inference_and_nothing_else_may():
    with pytest.raises(ValueError, match="allow_remote_inference"):
        Profile.model_validate(_profile(llm={"provider": "bedrock", "model": "m"}))
    with pytest.raises(ValueError, match="not authorized"):
        Profile.model_validate(_profile(llm={"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                                             "model": "m", "allow_remote_inference": True}))


def test_flipping_the_paid_flags_without_an_authorization_is_still_refused():
    with pytest.raises(ValueError, match="must be"):
        CostConfig(paid_llm_calls_allowed=True)


@pytest.mark.parametrize("name", [n[:-5] for n in os.listdir(os.path.join(REPO_ROOT, "config", "profiles"))
                                  if n.endswith(".yaml") and n != "fdm_agentcore.yaml"])
def test_no_other_profile_can_reach_a_paid_provider(name):
    import yaml

    with open(os.path.join(REPO_ROOT, "config", "profiles", f"{name}.yaml")) as f:
        raw = yaml.safe_load(f)
    assert raw.get("llm", {}).get("provider") != "bedrock"
    assert not (raw.get("cost") or {}).get("paid_authorization")


def test_get_model_bedrock_needs_the_deployment_key(monkeypatch):
    monkeypatch.delenv(PAID_AWS_KEY, raising=False)
    with pytest.raises(PermissionError, match=PAID_AWS_KEY):
        get_model(ModelConfig(mode="bedrock", model_id="m", region="eu-west-2"))


def test_get_model_bedrock_constructs_with_the_key_and_makes_no_call(monkeypatch):
    monkeypatch.setenv(PAID_AWS_KEY, "1")
    monkeypatch.setenv("DATAINSIGHTS_BEDROCK_GUARDRAIL_ID", "gr-123")
    model = get_model(ModelConfig(mode="bedrock", model_id="test.model-id", region="eu-west-2"))
    cfg = model.get_config()
    assert cfg["model_id"] == "test.model-id" and cfg["guardrail_id"] == "gr-123"


# --- the fully wired AgentCore profile ------------------------------------

class FakeS3:
    def __init__(self):
        self.uploaded: list[tuple[str, str]] = []

    def download_file(self, bucket, key, local):
        assert (bucket, key) == ("events-bucket", "raw/tender_events.csv")
        shutil.copy(EVENTS, local)

    def upload_file(self, local, bucket, key):
        assert os.path.exists(local)
        self.uploaded.append((bucket, key))


class FakeDynamo:
    def __init__(self):
        self.items: dict[tuple, dict] = {}

    class ConditionalCheckFailedException(Exception):
        pass

    def put_item(self, TableName, Item, ConditionExpression=None, ExpressionAttributeValues=None):
        k = (Item["pk"]["S"], Item["sk"]["S"])
        if ConditionExpression and k in self.items:
            now = int(ExpressionAttributeValues[":now"]["N"])
            if int(self.items[k]["expires_at"]["N"]) >= now:
                raise FakeDynamo.ConditionalCheckFailedException("ConditionalCheckFailed")
        self.items[k] = Item

    def delete_item(self, TableName, Key, ConditionExpression=None, ExpressionAttributeValues=None):
        self.items.pop((Key["pk"]["S"], Key["sk"]["S"]), None)


@pytest.fixture
def deployed(monkeypatch, tmp_path):
    for k, v in DEPLOY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATAINSIGHTS_SCRATCH_DIR", str(tmp_path))
    s3, ddb = FakeS3(), FakeDynamo()
    monkeypatch.setattr("datainsights.aws_backends._s3", lambda client=None: s3)
    monkeypatch.setattr(RunLedger, "client", property(lambda self: ddb))
    return s3, ddb


@pytest.mark.skipif(not os.path.exists(EVENTS), reason="FDM events not generated")
def test_fdm_agentcore_profile_builds_every_aws_backend(deployed):
    from datainsights.runtime import build_runtime
    from datainsights.sources.caching import RunScopedCache
    from datainsights.sources.sql_source import AthenaSource

    rt = build_runtime("fdm_agentcore")
    src = rt.source._source if isinstance(rt.source, RunScopedCache) else rt.source
    assert isinstance(src, AthenaSource)
    assert src.domain_schema_map == {"kernel": "di_kernel", "lending": "di_lending"}
    assert src.athena_workgroup == "di_agents"
    assert rt.model_config.mode == "bedrock" and rt.model_config.model_id == "test.model-id"
    assert os.path.exists(rt.event_source_path)
    assert rt.ledger_table == "di-ledger" and rt.output_s3_uri == "s3://insights-bucket/insights"


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_book_run_publishes_to_s3_and_records_the_run(deployed, monkeypatch):
    """The `book` action end to end on the LOCAL data, with only the
    output/state halves swapped for their AWS fakes."""
    from agents.entrypoint import invoke
    from datainsights.runtime import _load_fdm_profile

    s3, ddb = deployed
    local = _load_fdm_profile("fdm_local")
    local.state = local.state.model_copy(update={"backend": "dynamodb", "table_name": "di-ledger"})
    local.output = local.output.model_copy(update={"backend": "s3", "s3_uri": "s3://insights-bucket/insights"})
    monkeypatch.setattr("datainsights.runtime._load_fdm_profile", lambda name: local)

    out = invoke({"action": "book", "profile": "fdm_local"})
    assert out["n_recommendations"] > 0 and out["worklist_top"]
    keys = {k for _, k in s3.uploaded}
    for name in ("fdm_rm_worklist.csv", "fdm_rm_digest.md", "fdm_insights.json"):
        assert f"insights/latest/{name}" in keys
        assert f"insights/{out['as_of']}/{out['run_id']}/{name}" in keys
    runs = [i for (pk, _), i in ddb.items.items() if pk == "RUN#fdm_local"]
    assert len(runs) == 1 and runs[0]["status"]["S"] == "finished"
    assert not [k for k in ddb.items if k[0].startswith("LOCK#")]   # released


def test_ledger_lock_refuses_an_overlapping_run():
    ddb = FakeDynamo()
    ledger = RunLedger("t", client=ddb)
    ledger.acquire("p", "run-1")
    with pytest.raises(RunOverlapError):
        RunLedger("t", client=ddb).acquire("p", "run-2")
    ledger.release("p", "run-1")
    RunLedger("t", client=ddb).acquire("p", "run-2")


def test_an_expired_lock_does_not_wedge_the_book():
    ddb = FakeDynamo()
    RunLedger("t", client=ddb, lock_ttl_seconds=-10).acquire("p", "crashed")
    RunLedger("t", client=ddb).acquire("p", "next")


def test_fetch_refuses_protected_paths_and_prefixes(tmp_path):
    with pytest.raises(PermissionError):
        fetch_s3_object("s3://b/protected_evaluator_only/trigger_events.csv", str(tmp_path), client=FakeS3())
    with pytest.raises(ValueError, match="prefix"):
        fetch_s3_object("s3://b/raw/", str(tmp_path), client=FakeS3())


def test_publish_files_layout(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x")
    s3 = FakeS3()
    out = publish_files({"worklist": str(f)}, "s3://bkt/pre/", as_of="2025-10-04", run_id="r1", client=s3)
    assert out == {"worklist": "s3://bkt/pre/2025-10-04/r1/a.csv"}
    assert sorted(k for _, k in s3.uploaded) == ["pre/2025-10-04/r1/a.csv", "pre/latest/a.csv"]
    assert parse_s3_uri("s3://bkt") == ("bkt", "")


# --- Athena dialect ---------------------------------------------------------

def test_athena_params_are_named_and_dates_become_date_literals():
    sql, params = _athena_params('SELECT * FROM t WHERE "D" >= %s AND "D" <= %s AND "C" = %s',
                                 ["2025-01-01", "2025-02-01T00:00:00", "GBP"])
    assert sql == 'SELECT * FROM t WHERE "D" >= %(p0)s AND "D" <= %(p1)s AND "C" = %(p2)s'
    assert params == {"p0": date(2025, 1, 1), "p1": date(2025, 2, 1), "p2": "GBP"}
    formatter = pytest.importorskip("pyathena.formatter")

    rendered = formatter.DefaultParameterFormatter().format(sql, params)
    assert "DATE '2025-01-01'" in rendered and "'GBP'" in rendered


def test_athena_params_without_parameters_pass_through():
    assert _athena_params("SELECT 1", []) == ("SELECT 1", None)


# --- operator scripts and Terraform stay in step with the code --------------

@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_publish_raw_uploads_exactly_the_contract_tables_and_the_event_feed():
    from datainsights.deploy.lake_contract import lake_tables
    from datainsights.deploy.publish_raw import upload_plan

    buckets = {"kernel": "k", "lending": "l", "market_events": "e"}
    plan = upload_plan(FDM_DIR, EVENTS, buckets)
    assert len(plan) == len(lake_tables()["tables"]) + 1
    assert all("protected" not in local and "trigger" not in local for local, _, _ in plan)
    assert ("e", "raw/tender_events/tender_events.csv") in {(b, k) for _, b, k in plan}


def test_publish_raw_refuses_a_protected_data_dir(tmp_path):
    from datainsights.deploy.publish_raw import upload_plan
    from datainsights.sources.fdm_local import PROTECTED_PATH_MARKER

    with pytest.raises(PermissionError):
        upload_plan(str(tmp_path / PROTECTED_PATH_MARKER), EVENTS, {})


def test_terraform_sets_every_variable_the_agentcore_profile_needs():
    """config/profiles/fdm_agentcore.yaml's ${VAR}s must all be set on the
    runtime by infra/terraform/modules/agent_runtime -- otherwise the
    deployed agent fails at its first invocation, not at plan time.
    AWS_REGION is injected by AgentCore itself."""
    import re

    with open(os.path.join(REPO_ROOT, "config", "profiles", "fdm_agentcore.yaml")) as f:
        profile = "".join(line.split("#")[0] + "\n" for line in f)
    needed = set(re.findall(r"\$\{([A-Z0-9_]+)\}", profile)) - {"AWS_REGION"}
    # connection_ref ATHENA -> SqlSource reads ATHENA_* (datainsights/sources/sql_source.py)
    needed |= {"ATHENA_REGION", "ATHENA_S3_STAGING_DIR", "DATAINSIGHTS_PROFILE", "DATAINSIGHTS_AGENTCORE_SERVE",
               PAID_AWS_KEY}
    with open(os.path.join(REPO_ROOT, "infra", "terraform", "modules", "agent_runtime", "main.tf")) as f:
        tf = f.read()
    env_block = tf[tf.index("environment_variables = {"):]
    env_block = env_block[:env_block.index("\n  }")]
    set_by_tf = set(re.findall(r"^\s*([A-Z0-9_]+)\s*=", env_block, re.M))
    assert needed <= set_by_tf, f"not set by Terraform: {sorted(needed - set_by_tf)}"
