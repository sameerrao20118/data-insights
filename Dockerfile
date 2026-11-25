# AgentCore Runtime image (docs/aws_deployment.md). Built for linux/arm64 --
# AgentCore Runtime runs ARM64 containers only -- by
# .github/workflows/agent-image.yml, pushed to the ECR repository that
# infra/terraform/stacks/10-foundation creates, and run by the
# aws_bedrockagentcore_agent_runtime in infra/terraform/stacks/30-agents.
#
# What is NOT in this image, on purpose: any data. The agents read the
# curated lake through Athena; generated datasets, holdouts and every
# protected_evaluator_only path are excluded by .dockerignore AND are never
# COPY'd (only the code packages below are). The image cannot see labels.
#
# NOT RUN: never built or pushed from this repo until you run the workflow.

FROM --platform=linux/arm64 python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATAINSIGHTS_SCRATCH_DIR=/tmp/datainsights

WORKDIR /app

COPY requirements.txt requirements-aws.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-aws.txt

# Code and config only -- the runtime needs nothing else.
COPY agents/ agents/
COPY detection_engine/ detection_engine/
COPY datainsights/ datainsights/
COPY external_events/*.py external_events/
COPY config/ config/
COPY prompts/ prompts/

RUN useradd --uid 10001 --no-create-home agent && mkdir -p /tmp/datainsights && chown agent /tmp/datainsights
USER agent

# AgentCore Runtime's HTTP contract: POST /invocations, GET /ping on 8080,
# served by bedrock_agentcore.runtime.BedrockAgentCoreApp (agents/entrypoint.py).
EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "-m", "agents.entrypoint"]
