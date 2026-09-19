"""
T8 (docs/ml_strategy_plan.md §9) -- both cloud ML training backends must
construct cleanly (no network call) and raise NotImplementedError naming
their exact blocker when actually invoked, never silently falling back
to local training.
"""

from __future__ import annotations

import pytest

from datainsights.ml.backends.sagemaker_training import SageMakerTrainingConfig, train as sagemaker_train
from datainsights.ml.backends.snowpark_training import SnowparkTrainingConfig, train as snowpark_train
from datainsights.ml.model_registry import ModelCard

CARD = ModelCard(purpose="x", data_window="x", features=["x"], metrics={}, limitations="x", owner="x")


def test_sagemaker_config_constructs_without_a_network_call():
    config = SageMakerTrainingConfig(role_arn="arn:aws:iam::x", training_image="x",
                                     instance_type="ml.m5.large", output_s3_uri="s3://x",
                                     model_registry_group="x")
    assert config.role_arn == "arn:aws:iam::x"


def test_sagemaker_train_raises_naming_the_blocker():
    config = SageMakerTrainingConfig(role_arn="x", training_image="x", instance_type="x",
                                     output_s3_uri="x", model_registry_group="x")
    with pytest.raises(NotImplementedError, match="credentials"):
        sagemaker_train(config, "x", CARD)


def test_snowpark_config_constructs_without_a_network_call():
    config = SnowparkTrainingConfig(warehouse="x", database="x", schema="x", stage="x")
    assert config.warehouse == "x"


def test_snowpark_train_raises_naming_the_blocker():
    config = SnowparkTrainingConfig(warehouse="x", database="x", schema="x", stage="x")
    with pytest.raises(NotImplementedError, match="credentials"):
        snowpark_train(config, "x", CARD)
