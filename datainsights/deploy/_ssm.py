"""Service discovery for the operator scripts: every name Terraform creates
is written to SSM Parameter Store under /<project>/<environment>/... by the
stack that owns it (infra/terraform/stacks/*/outputs.tf)."""

from __future__ import annotations


def params(project: str, environment: str, *, client=None) -> dict[str, str]:
    """All parameters under /<project>/<environment>/, keyed by the path
    after that prefix (e.g. "lake/kernel/bucket")."""
    if client is None:
        import boto3

        client = boto3.client("ssm")
    prefix = f"/{project}/{environment}/"
    out, token = {}, None
    while True:
        kw = {"Path": prefix, "Recursive": True, "MaxResults": 10}
        if token:
            kw["NextToken"] = token
        page = client.get_parameters_by_path(**kw)
        out.update({p["Name"][len(prefix):]: p["Value"] for p in page["Parameters"]})
        token = page.get("NextToken")
        if not token:
            return out


def require(found: dict[str, str], key: str, stack: str) -> str:
    if key not in found:
        raise SystemExit(f"SSM parameter {key!r} not found -- has infra/terraform/stacks/{stack} been applied?")
    return found[key]
