# CONTRACT ONLY -- NEVER BUILT OR PUSHED THIS SESSION.
#
# Packaging shape for docs/decision_record.md's Stage 3 ("Swap OllamaModel
# -> internal Model Gateway. Containerise. Enter governance with a working
# artefact and evidence."). This exists so the shape is agreed now, not
# invented later under governance time pressure -- it is not a statement
# that this project has containerised, deployed, or run anything in AWS.
# Per CLAUDE.md: "AWS/Bedrock/AgentCore adapters are contract-and-mock
# only until explicitly authorized."
#
# Verified this session (not assumed): all packages below dry-run install
# cleanly on this machine against the exact versions confirmed on the
# bank's Artifactory (docs/decision_record.md Tab 7).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ agents/
COPY detection_engine/ detection_engine/
COPY datainsights/ datainsights/
COPY config/ config/
COPY data_generator/ data_generator/

# Stage 3 only: agents/model_factory.ModelConfig.mode="model_gateway"
# replaces the local Ollama call with the bank's internal Model Gateway --
# that branch is currently NotImplementedError by design (see
# agents/model_factory.py) until governance sign-off exists. No Ollama
# server runs inside this image; Stage 1/2 local development uses this
# machine's own Ollama installation directly, outside any container.

# AgentCore Runtime supplies its own process entrypoint via
# bedrock_agentcore.runtime.BedrockAgentCoreApp (see agents/entrypoint.py).
# This CMD is documentation of the intended shape, not a verified command
# -- it has never been run.
CMD ["python", "-m", "agents.entrypoint"]
