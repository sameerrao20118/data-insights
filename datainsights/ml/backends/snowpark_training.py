"""
T8 -- Snowpark ML training backend contract. NOT RUN. docs/ml_strategy_plan.md
§7: training near the data, for the single case where moving a book's
feature table out of Snowflake is the expensive part (E4 propensity at
real bank scale) -- ordinary sklearn running inside a Snowpark Python
UDF/stored procedure, not a hosted LLM/AI service.

Explicit boundary (do not read this module's existence as clearing it):
CLAUDE.md forbids Snowflake Cortex/AI_COMPLETE -- that is paid LLM
inference and stays forbidden. Snowpark ML running plain sklearn is a
DIFFERENT category (ordinary compute, no model-provider call), but it is
NOT pre-authorized by that distinction alone -- it needs its own explicit
sign-off before any code targets it, same as every other AWS/cloud
resource this repo touches.
"""

from __future__ import annotations

from dataclasses import dataclass

from datainsights.ml.model_registry import ModelCard


@dataclass
class SnowparkTrainingConfig:
    warehouse: str
    database: str
    schema: str
    stage: str  # where the trained model artifact lands


def train(config: SnowparkTrainingConfig, feature_table: str, card: ModelCard) -> str:
    """Contract-only, same discipline as sagemaker_training.train() --
    constructing SnowparkTrainingConfig never touches a network; calling
    train() always raises here, naming the exact blocker."""
    raise NotImplementedError(
        "Snowpark ML training is contract-only (docs/ml_strategy_plan.md §7/§9 T8) -- "
        "no Snowflake credentials configured (FdmSnowflakeSource has never run, see "
        "docs/gap_analysis.md), and this specific use -- Snowpark ML, not Cortex/"
        "AI_COMPLETE -- has NOT been separately authorized under CLAUDE.md's explicit-"
        "authorization requirement even though it is a different category from the "
        "forbidden paid-LLM path. Do not build the real Snowpark session/UDF wiring "
        "until that authorization exists."
    )
