"""
T8 -- SageMaker training backend contract. NOT RUN. docs/ml_strategy_plan.md
§6: the legitimate SageMaker case is the SINGLE whole-book E4 propensity
model (once RM-feedback labels exist, D6), trained periodically via a
SageMaker Training Job, versioned in SageMaker Model Registry, and scored
in batch via Batch Transform -- never thousands of per-client E2
baseline endpoints, which is a cost/latency anti-pattern at this shape.

See datainsights/ml/model_registry.py's ModelCard -- it already carries
purpose/data_window/features/metrics/limitations/owner, which is close
to what SageMaker Model Registry + MRM ask for. Keep ModelCard as the
source of truth; this backend's job (once built) is to also register
there, not to replace it.
"""

from __future__ import annotations

from dataclasses import dataclass

from datainsights.ml.model_registry import ModelCard


@dataclass
class SageMakerTrainingConfig:
    role_arn: str
    training_image: str
    instance_type: str
    output_s3_uri: str
    model_registry_group: str


def train(config: SageMakerTrainingConfig, training_table_path: str, card: ModelCard) -> str:
    """Contract-only. Constructing a SageMakerTrainingConfig is safe (no
    network call); calling train() always raises here, naming the exact
    blocker, exactly like datainsights/runtime.py's s3_parquet/glue_athena
    backends do for the source layer. Building the real boto3/sagemaker
    SDK call is real, scoped, undone work -- gated on: (1) RM-feedback
    label volume sufficient to train E4 at all (D6, currently blocked on
    access), (2) AWS credentials, (3) explicit authorization per
    CLAUDE.md's 'AWS/Bedrock/AgentCore adapters are contract-and-mock
    only until explicitly authorized.'"""
    raise NotImplementedError(
        "SageMaker training is contract-only (docs/ml_strategy_plan.md §6/§9 T8) -- "
        "no AWS credentials configured, no explicit authorization received per CLAUDE.md, "
        "and datainsights.ml.label_pipeline's training table is still empty pending real "
        "RM feedback volume (D6). Construct SageMakerTrainingConfig and call train() again "
        "once all three are true; this function will not silently fall back to local training."
    )
