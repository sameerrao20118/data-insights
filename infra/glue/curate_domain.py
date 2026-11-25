"""
Glue 5.1 job: one data-mesh domain's raw CSV drop -> its curated data
product (infra/terraform/modules/lake_domain creates one job per domain).

For every table of the domain in lake_tables.json (generated from
config/entities_fdm.yaml by datainsights/deploy/lake_contract.py):
  1. read raw/<table>/ as all-string CSV (no inference -- the contract
     decides types, not the file);
  2. keep ONLY contracted columns, lower-cased, cast to the contract type;
     a non-empty raw value that fails its cast fails the job (no silent nulls);
  3. evaluate the table's Glue Data Quality ruleset (same DQDL Terraform
     attaches to the catalog table) and fail the job on any failed rule;
  4. overwrite curated/<table>/ as Parquet. The Glue Catalog table itself
     is owned by Terraform, so schema never drifts from the contract.

tests/test_aws_read_path.py proves the same load (DuckDB, same casts)
yields identical recommendations. This script itself is NOT RUN until you
start the job.

ref: docs.aws.amazon.com/glue/latest/dg/release-notes.html (Glue 5.1)
ref: docs.aws.amazon.com/glue/latest/dg/glue-data-quality-api.html (EvaluateDataQuality)
"""

import json
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from awsgluedq.transforms import EvaluateDataQuality
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(sys.argv, ["JOB_NAME", "domain", "lake_spec_uri", "dq_rulesets_uri", "lake_bucket"])
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)
s3 = boto3.client("s3")


def read_json(uri: str) -> dict:
    bucket, _, key = uri[len("s3://"):].partition("/")
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


spec = read_json(args["lake_spec_uri"])
rulesets = read_json(args["dq_rulesets_uri"])
domain = args["domain"]
root = f"s3://{args['lake_bucket']}"
tables = [t for t in spec["tables"] if t["domain"] == domain]
if not tables:
    raise ValueError(f"lake spec has no tables for domain {domain!r}")

for t in tables:
    name = t["table"]
    raw = (spark.read.option("header", "true").option("inferSchema", "false")
           .option("multiLine", "true").option("escape", '"').csv(f"{root}/raw/{name}/"))
    cols, bad_cast_checks = [], []
    for c in t["columns"]:
        src = c["source_name"]
        if src in raw.columns:
            value = F.when(F.trim(F.col(src)) == "", None).otherwise(F.col(src))
            casted = value.cast(c["type"])
            bad_cast_checks.append(F.sum(F.when(value.isNotNull() & casted.isNull(), 1).otherwise(0)).alias(c["name"]))
        else:
            casted = F.lit(None).cast(c["type"])
        cols.append(casted.alias(c["name"]))

    if bad_cast_checks:
        bad = raw.agg(*bad_cast_checks).collect()[0].asDict()
        bad = {k: v for k, v in bad.items() if v}
        if bad:
            raise ValueError(f"{domain}.{name}: values that do not cast to the contract type: {bad}")

    curated = raw.select(*cols)
    results = EvaluateDataQuality.apply(
        frame=DynamicFrame.fromDF(curated, glue, name),
        ruleset=rulesets[name],
        publishing_options={
            "dataQualityEvaluationContext": f"{domain}_{name}",
            "enableDataQualityCloudWatchMetrics": True,
            "enableDataQualityResultsPublishing": True,
        },
    ).toDF()
    failed = [r.asDict() for r in results.filter(F.col("Outcome") != "Passed").collect()]
    if failed:
        raise ValueError(f"{domain}.{name}: data quality failed: {failed}")

    curated.write.mode("overwrite").parquet(f"{root}/curated/{name}/")
    print(f"{domain}.{name}: {curated.count()} rows curated, {len(results.collect())} DQ rules passed")

job.commit()
