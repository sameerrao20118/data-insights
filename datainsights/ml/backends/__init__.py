"""
T8 (docs/ml_strategy_plan.md §9) -- AWS/Snowflake ML training backend
contracts. Same discipline as datainsights/runtime.py's cloud source
backends: construct from a profile, then raise NotImplementedError
naming the EXACT blocker (no credentials, no authorization) -- never a
silent local fallback. Nothing in this package makes a network call.

docs/ml_strategy_plan.md §6 explains WHY these two, specifically, and
not others: SageMaker for the single whole-book E4 propensity model
(training job + model registry + batch transform, once RM-feedback
labels exist), Snowpark ML for training close to the data when a book's
feature table is too large to move. Neither is authorized today --
CLAUDE.md requires explicit authorization for any AWS/cloud resource,
and this package's existence is not that authorization.
"""
