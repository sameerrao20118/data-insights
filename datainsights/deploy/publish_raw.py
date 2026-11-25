"""
Producer step: drop the generated synthetic data into each domain's raw
zone, then (with --curate) run each domain's Glue curation job.

    python -m datainsights.deploy.publish_raw                       # upload only
    python -m datainsights.deploy.publish_raw --curate              # + run Glue, wait
    python -m datainsights.deploy.publish_raw --data-dir data_generator/output_fdm_scaled \\
        --events external_events/output_fdm_scaled/tender_events.csv --curate

What is uploaded is an ALLOW-LIST: exactly the tables in lake_tables.json
(i.e. the source contract) plus the one events file. Nothing else under the
data directory is ever read -- holdouts, the evaluator-only label path
(PROTECTED_PATH_MARKER) and the generator's labels cannot be uploaded by
this script, by construction and by an explicit refusal.

Uses YOUR AWS credentials (whatever boto3 resolves). NOT RUN until you run it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from datainsights.deploy._ssm import params, require
from datainsights.deploy.lake_contract import REPO_ROOT, lake_tables
from datainsights.sources.fdm_local import PROTECTED_PATH_MARKER

DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
DEFAULT_EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")
EVENTS_DOMAIN = "market_events"


def upload_plan(data_dir: str, events_path: str, buckets: dict[str, str]) -> list[tuple[str, str, str]]:
    """(local path, bucket, key) for every file to upload -- the allow-list."""
    for p in (data_dir, events_path):
        if PROTECTED_PATH_MARKER in os.path.abspath(p):
            raise PermissionError(f"refusing to publish from a protected path: {p}")
    plan = []
    for t in lake_tables()["tables"]:
        local = os.path.join(data_dir, t["domain"], f"{t['table']}.csv")
        if not os.path.exists(local):
            raise FileNotFoundError(f"{local} missing -- generate the data first (docs/current_state.md)")
        if t["domain"] not in buckets:
            raise KeyError(f"no lake bucket for domain {t['domain']!r} -- is it in 10-foundation's data_domains?")
        plan.append((local, buckets[t["domain"]], f"raw/{t['table']}/{t['table']}.csv"))
    if not os.path.exists(events_path):
        raise FileNotFoundError(f"{events_path} missing -- python -m data_generator.fdm.generate_fdm_events")
    plan.append((events_path, buckets[EVENTS_DOMAIN], "raw/tender_events/tender_events.csv"))
    return plan


def run_glue(jobs: dict[str, str], *, client=None, poll_seconds: int = 20) -> bool:
    if client is None:
        import boto3

        client = boto3.client("glue")
    runs = {d: client.start_job_run(JobName=j)["JobRunId"] for d, j in jobs.items()}
    print(f"started {len(runs)} curation job(s): {', '.join(jobs.values())}")
    pending, ok = dict(runs), True
    while pending:
        time.sleep(poll_seconds)
        for d, run_id in list(pending.items()):
            run = client.get_job_run(JobName=jobs[d], RunId=run_id)["JobRun"]
            if run["JobRunState"] in ("SUCCEEDED", "FAILED", "TIMEOUT", "STOPPED", "ERROR"):
                print(f"  {d}: {run['JobRunState']} {run.get('ErrorMessage', '')}")
                ok &= run["JobRunState"] == "SUCCEEDED"
                del pending[d]
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="datainsights")
    ap.add_argument("--environment", default="dev")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--events", default=DEFAULT_EVENTS)
    ap.add_argument("--curate", action="store_true", help="run each domain's Glue job and wait")
    args = ap.parse_args()

    import boto3

    found = params(args.project, args.environment)
    domains = {k.split("/")[1] for k in found if k.startswith("lake/") and k.endswith("/bucket")}
    buckets = {d: require(found, f"lake/{d}/bucket", "10-foundation") for d in domains}
    plan = upload_plan(args.data_dir, args.events, buckets)
    s3 = boto3.client("s3")
    for local, bucket, key in plan:
        s3.upload_file(local, bucket, key)
        print(f"  s3://{bucket}/{key}  <- {os.path.relpath(local, REPO_ROOT)}")
    print(f"uploaded {len(plan)} files")

    if args.curate:
        jobs = {k.split("/")[1]: v for k, v in found.items() if k.startswith("mesh/") and k.endswith("/glue_job")}
        if not jobs:
            raise SystemExit("no Glue jobs in SSM -- has infra/terraform/stacks/20-data-mesh been applied?")
        sys.exit(0 if run_glue(jobs) else 1)


if __name__ == "__main__":
    main()
