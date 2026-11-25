"""
Data-mesh step after `20-data-mesh` applies: make each producer domain's
Glue database a published data product in SageMaker Unified Studio.

    python -m datainsights.deploy.smus_catalog

For each producer project (SSM /<project>/<env>/smus/project/<domain>):
  1. find the project's Lakehouse environment and the project role it runs as;
  2. grant that role, and the domain's manage-access role, Lake Formation
     DESCRIBE/SELECT on the domain's database WITH GRANT OPTION -- so SMUS
     can grant subscribers later (a consumer never gets a manual grant);
  3. create (or reuse) a GLUE data source for the database with
     publish-on-import and DQ-result import, and start a run.

Then, in the SMUS portal, the `nba-agents` consumer project searches the
catalog, subscribes to the producer assets, and the producer approves --
the data-mesh loop, by hand on purpose (docs/aws_deployment.md step 6).

Business-name generation is OFF: it is a generative-AI feature (paid
inference) and nothing here needs it.

Why a script and not Terraform: the project roles are created by SMUS when
the project's environments deploy, after Terraform's apply; Terraform
cannot for_each over names it only learns then.

NOT RUN. The DataZone API shapes follow the boto3 `datazone` client; if
SMUS has moved on, the portal path in docs/aws_deployment.md does the same
three steps.
"""

from __future__ import annotations

import argparse

from datainsights.deploy._ssm import params, require

CONSUMER = "nba-agents"


def _lakehouse_environment(dz, domain_id: str, project_id: str) -> dict:
    envs = dz.list_environments(domainIdentifier=domain_id, projectIdentifier=project_id)["items"]
    for e in envs:
        if "lakehouse" in e["name"].lower() or "datalake" in e["name"].lower():
            return dz.get_environment(domainIdentifier=domain_id, identifier=e["id"])
    raise SystemExit(f"project {project_id}: no Lakehouse environment yet ({[e['name'] for e in envs]}) -- "
                     "wait for the project's environments to finish deploying, then re-run")


def _project_role(env: dict) -> str:
    for r in env.get("provisionedResources", []):
        if r.get("name") in ("userRoleArn", "projectRoleArn") or r.get("type") == "IAMRole":
            return r["value"]
    raise SystemExit(f"environment {env['id']}: no project role in provisionedResources")


def _grant(lf, principal: str, database: str) -> None:
    lf.grant_permissions(Principal={"DataLakePrincipalIdentifier": principal},
                         Resource={"Database": {"Name": database}},
                         Permissions=["DESCRIBE"], PermissionsWithGrantOption=["DESCRIBE"])
    lf.grant_permissions(Principal={"DataLakePrincipalIdentifier": principal},
                         Resource={"Table": {"DatabaseName": database, "TableWildcard": {}}},
                         Permissions=["SELECT", "DESCRIBE"], PermissionsWithGrantOption=["SELECT", "DESCRIBE"])


def _data_source(dz, domain_id: str, project_id: str, env_id: str, database: str) -> str:
    name = f"{database}-glue"
    for ds in dz.list_data_sources(domainIdentifier=domain_id, projectIdentifier=project_id)["items"]:
        if ds["name"] == name:
            return ds["dataSourceId"]
    created = dz.create_data_source(
        domainIdentifier=domain_id, projectIdentifier=project_id, environmentIdentifier=env_id,
        name=name, type="GLUE", description=f"Curated data product of {database}",
        configuration={"glueRunConfiguration": {
            "relationalFilterConfigurations": [{"databaseName": database}],
            "autoImportDataQualityResult": True,
        }},
        publishOnImport=True, enableSetting="ENABLED",
        recommendation={"enableBusinessNameGeneration": False},
    )
    return created["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="datainsights")
    ap.add_argument("--environment", default="dev")
    args = ap.parse_args()

    import boto3

    found = params(args.project, args.environment)
    domain_id = require(found, "smus/domain_id", "20-data-mesh")
    projects = {k.split("/")[-1]: v for k, v in found.items() if k.startswith("smus/project/")}
    manage_access = boto3.client("iam").get_role(
        RoleName=f"{args.project}-{args.environment}-smus-manage-access")["Role"]["Arn"]
    dz, lf = boto3.client("datazone"), boto3.client("lakeformation")

    for domain, project_id in sorted(projects.items()):
        if domain == CONSUMER:
            continue
        database = require(found, f"mesh/{domain}/glue_database", "20-data-mesh")
        env = _lakehouse_environment(dz, domain_id, project_id)
        for principal in (_project_role(env), manage_access):
            _grant(lf, principal, database)
        ds_id = _data_source(dz, domain_id, project_id, env["id"], database)
        run = dz.start_data_source_run(domainIdentifier=domain_id, dataSourceIdentifier=ds_id)
        print(f"{domain}: {database} -> data source {ds_id}, run {run['id']} ({run['status']})")
    print("Next: SMUS portal -> project nba-agents -> Discover -> subscribe to the published assets.")


if __name__ == "__main__":
    main()
