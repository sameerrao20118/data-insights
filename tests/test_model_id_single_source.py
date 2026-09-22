"""
R20 (docs/refactor_plan.md §6h): the model id is sourced from the active
profile only. A literal Ollama model tag anywhere else in the code base
is the "pinned in 12 places" problem coming back.
"""

from __future__ import annotations

import os
import re

from agents.model_factory import ModelConfig, default_model_id
from datainsights.runtime import active_profile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("agents", "datainsights", "detection_engine", "external_events", "onboarding",
             "dashboard", "evaluation", "narrative", "tests")
# A local model tag: name, colon, size/variant -- e.g. qwen2.5:7b, llama3:8b.
MODEL_TAG = re.compile(r"""["'](?:qwen|llama|mistral|gemma|phi|deepseek)[\w.\-]*:[\w.\-]+["']""")
ALLOWED = {
    # the refusal test needs a "-cloud" tag to prove it is refused
    "tests/test_domain_agent.py",
    # profile fixtures ARE profiles -- the id legitimately lives there
    "tests/test_runtime.py",
    # this file: the task_models refusal test below likewise has to name a
    # "-cloud" tag in order to assert it is rejected
    "tests/test_model_id_single_source.py",
}


def _py_files():
    for d in SCAN_DIRS:
        root = os.path.join(REPO_ROOT, d)
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.relpath(os.path.join(dirpath, f), REPO_ROOT)


def test_no_python_file_names_a_model_outside_the_profiles():
    offenders = []
    for rel in _py_files():
        if rel in ALLOWED:
            continue
        with open(os.path.join(REPO_ROOT, rel)) as fh:
            for n, line in enumerate(fh, 1):
                code = line.split("#", 1)[0]
                if MODEL_TAG.search(code):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "model ids must come from the profile only:\n" + "\n".join(offenders)


def test_default_model_config_reads_the_active_profile():
    assert ModelConfig(mode="local").model_id == active_profile().llm.model == default_model_id()


def test_profile_selection_changes_the_default(monkeypatch):
    monkeypatch.setenv("DATAINSIGHTS_PROFILE", "sba_local")
    assert ModelConfig(mode="local").model_id == active_profile("sba_local").llm.model


def test_task_models_are_read_from_the_profile_not_python():
    """Per-task model selection must not reintroduce the problem R20
    solved. model_id_for() resolves a task's tag from the profile; no
    Python file may name one (the scan above already enforces that)."""
    from agents.model_factory import model_id_for

    profile = active_profile()
    for task, expected in (profile.llm.task_models or {}).items():
        assert model_id_for(task) == expected
    # An unmapped task falls back to the profile default, never to a literal.
    assert model_id_for("a_task_nobody_configured") == profile.llm.model


def test_a_cloud_tag_is_refused_in_a_task_override_too():
    """task_models must not become a way around the cost policy: the same
    -cloud refusal that guards `model` has to guard every override."""
    import pytest

    from datainsights.config import LLMConfig

    with pytest.raises(ValueError, match="cloud-routed"):
        LLMConfig(provider="ollama", base_url="http://127.0.0.1:11434",
                  model="qwen2.5:7b", task_models={"proposer": "gpt-oss:20b-cloud"})
