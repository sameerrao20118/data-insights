"""
The AWS halves of the profile's event_source / state / output blocks --
what config/profiles/fdm_agentcore.yaml selects and infra/terraform
provisions (docs/aws_deployment.md).

Deliberately thin. Nothing above datainsights/runtime.py changes: the
pipeline still reads an events CSV path, writes trace/run sqlite files
and worklist files to a directory. On AWS those are an ephemeral scratch
directory inside the AgentCore session, and this module moves the bytes
in and out:

  * events   s3://.../tender_events.csv  -> scratch file  (fetch_s3_object)
  * outputs  scratch files -> s3://.../insights/<as_of>/<run_id>/  (publish_files)
  * ledger   one DynamoDB item per run, plus a conditional-write lock
             honouring monitor.max_concurrent_runs=1 across sessions
             (RunLedger) -- the cross-process counterpart of runs.RunLock,
             which only sees its own sqlite file.

What the ledger does NOT do: carry the incremental watermark between
AgentCore sessions. Each session starts with an empty scratch directory,
so every book run on AWS is a full run (incremental=False) -- correct,
just not incremental. Persisting runs.db is a later step, not faked here.

boto3 is imported lazily and every function takes an optional client so
tests run with fakes and no credentials. STATUS: NOT RUN against AWS.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal


def scratch_dir(*parts: str) -> str:
    """Per-process writable scratch -- /tmp inside the AgentCore container."""
    root = os.environ.get("DATAINSIGHTS_SCRATCH_DIR") or os.path.join(tempfile.gettempdir(), "datainsights")
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// uri: {uri!r}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket:
        raise ValueError(f"no bucket in {uri!r}")
    return bucket, key


def _s3(client):
    if client is not None:
        return client
    import boto3

    return boto3.client("s3")


def fetch_s3_object(uri: str, dest_dir: str, *, client=None) -> str:
    """Download one object; returns the local path. Refuses the protected
    evaluator-only path even if a profile is misconfigured to point at it."""
    from datainsights.sources.fdm_local import PROTECTED_PATH_MARKER

    if PROTECTED_PATH_MARKER in uri:
        raise PermissionError(f"refusing to fetch protected evaluator-only data: {uri}")
    bucket, key = parse_s3_uri(uri)
    if not key or key.endswith("/"):
        raise ValueError(f"event_source.s3_uri must name an object, not a prefix: {uri!r}")
    local = os.path.join(dest_dir, os.path.basename(key))
    _s3(client).download_file(bucket, key, local)
    return local


def publish_files(paths: dict[str, str], s3_uri: str, *, as_of: str, run_id: str, client=None) -> dict[str, str]:
    """Upload each local file under <s3_uri>/<as_of>/<run_id>/ and also to
    <s3_uri>/latest/ (what a consumer -- the SMUS consumer project, a
    dashboard -- reads without knowing run ids). Returns name -> s3 uri."""
    bucket, prefix = parse_s3_uri(s3_uri)
    prefix = prefix.rstrip("/")
    s3 = _s3(client)
    out = {}
    for name, local in paths.items():
        base = os.path.basename(local)
        for folder in (f"{as_of}/{run_id}", "latest"):
            key = "/".join(p for p in (prefix, folder, base) if p)
            s3.upload_file(local, bucket, key)
        out[name] = f"s3://{bucket}/{'/'.join(p for p in (prefix, as_of, run_id, base) if p)}"
    return out


class RunOverlapError(RuntimeError):
    pass


class RunLedger:
    """DynamoDB table with partition key `pk` and sort key `sk` (created by
    infra/terraform/modules/agent_runtime). Items:
        pk=RUN#<profile>   sk=<started_at>#<run_id>   one per run
        pk=LOCK#<profile>  sk=LOCK                     present while a run holds the lock
    """

    def __init__(self, table_name: str, *, client=None, lock_ttl_seconds: int = 900):
        self.table_name = table_name
        self._client = client
        self.lock_ttl_seconds = lock_ttl_seconds

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("dynamodb")
        return self._client

    def acquire(self, profile: str, run_id: str) -> None:
        """Conditional put: succeeds if no lock exists or the existing one
        has expired (a crashed session must not wedge the book forever)."""
        now = int(time.time())
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={"pk": {"S": f"LOCK#{profile}"}, "sk": {"S": "LOCK"}, "run_id": {"S": run_id},
                      "expires_at": {"N": str(now + self.lock_ttl_seconds)}},
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
        except Exception as e:  # botocore ConditionalCheckFailedException, without importing botocore
            if type(e).__name__ == "ConditionalCheckFailedException" or "ConditionalCheckFailed" in str(e):
                raise RunOverlapError(f"another {profile!r} run holds the lock -- max_concurrent_runs=1") from e
            raise

    def release(self, profile: str, run_id: str) -> None:
        try:
            self.client.delete_item(
                TableName=self.table_name, Key={"pk": {"S": f"LOCK#{profile}"}, "sk": {"S": "LOCK"}},
                ConditionExpression="run_id = :r", ExpressionAttributeValues={":r": {"S": run_id}},
            )
        except Exception as e:
            if type(e).__name__ != "ConditionalCheckFailedException" and "ConditionalCheckFailed" not in str(e):
                raise

    def record(self, profile: str, run_id: str, *, status: str, summary: dict) -> None:
        started = summary.get("started_at") or datetime.now(timezone.utc).isoformat()
        item = {"pk": {"S": f"RUN#{profile}"}, "sk": {"S": f"{started}#{run_id}"},
                "run_id": {"S": run_id}, "status": {"S": status},
                "summary": {"S": json.dumps(summary, default=_json_default, sort_keys=True)}}
        self.client.put_item(TableName=self.table_name, Item=item)


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)
