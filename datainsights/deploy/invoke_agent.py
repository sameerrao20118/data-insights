"""
Call the deployed NBA agent on AgentCore Runtime.

    python -m datainsights.deploy.invoke_agent --action book            # whole book -> S3 worklist
    python -m datainsights.deploy.invoke_agent --prty-id PRTY00036      # one client, Bedrock-narrated
    python -m datainsights.deploy.invoke_agent --prty-id PRTY00036 --no-narrate

The runtime ARN comes from SSM (/<project>/<env>/agent/runtime_arn, written
by infra/terraform/stacks/30-agents). Uses YOUR AWS credentials; a
narrated call is a billed Bedrock call. NOT RUN until you run it.
"""

from __future__ import annotations

import argparse
import json
import uuid

from datainsights.deploy._ssm import params, require


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="datainsights")
    ap.add_argument("--environment", default="dev")
    ap.add_argument("--action", choices=["client", "book"], default="client")
    ap.add_argument("--prty-id")
    ap.add_argument("--as-of")
    ap.add_argument("--domain", default="all")
    ap.add_argument("--no-narrate", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if args.action == "client" and not args.prty_id:
        ap.error("--prty-id is required for --action client")

    payload = {"action": args.action}
    if args.as_of:
        payload["as_of"] = args.as_of
    if args.action == "client":
        payload.update(prty_id=args.prty_id, domain=args.domain, narrate=not args.no_narrate)
    else:
        payload["top"] = args.top

    import boto3

    arn = require(params(args.project, args.environment), "agent/runtime_arn", "30-agents")
    resp = boto3.client("bedrock-agentcore").invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"datainsights-{uuid.uuid4()}",   # AgentCore requires >= 33 chars
        payload=json.dumps(payload).encode(),
        qualifier="DEFAULT",
    )
    body = resp["response"].read()
    try:
        print(json.dumps(json.loads(body), indent=2))
    except ValueError:
        print(body.decode(errors="replace"))


if __name__ == "__main__":
    main()
