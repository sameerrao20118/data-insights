"""
SLOT E4 readiness, no model (docs/generalization_plan.md Phase 4) -- a
local, file-based model registry: `var/models/<name>/<version>/
{model.joblib, card.json}`. Exists so the day a real model trains on
datainsights.ml.label_pipeline's training table, there's already a
place for it with a model card, not an ad-hoc pickle someone emails
around. Empty until then -- registering a model here is not this
repo's call to make; no model is trained by any code in this
directory.

The model card is the actual governance artifact: purpose, data window,
features, metrics, limitations, owner. A model file without one is
refused, not silently accepted -- the same "no untracked artifact"
discipline `datainsights/ml/scale_evaluation.py`'s run manifests
already hold ML runs to.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

import joblib

REGISTRY_ROOT_DEFAULT = os.path.join("var", "models")

REQUIRED_CARD_FIELDS = ("purpose", "data_window", "features", "metrics", "limitations", "owner")


@dataclass
class ModelCard:
    purpose: str
    data_window: str
    features: list[str]
    metrics: dict
    limitations: str
    owner: str
    trained_at: str = ""
    extra: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        problems = []
        for f in REQUIRED_CARD_FIELDS:
            value = getattr(self, f)
            if not value:
                problems.append(f"model card missing required field: {f!r}")
        return problems


class ModelRegistryError(Exception):
    pass


def _version_dir(name: str, version: str, root: str) -> str:
    return os.path.join(root, name, version)


def save_model(name: str, version: str, model, card: ModelCard, *, root: str = REGISTRY_ROOT_DEFAULT) -> str:
    """Writes model.joblib + card.json. Refuses to save without a valid
    card -- see ModelCard.validate(). Overwrites an existing
    (name, version) directory; callers wanting immutable versions should
    pick a new version string, same discipline a package registry would
    hold them to."""
    problems = card.validate()
    if problems:
        raise ModelRegistryError("; ".join(problems))

    out_dir = _version_dir(name, version, root)
    os.makedirs(out_dir, exist_ok=True)
    joblib.dump(model, os.path.join(out_dir, "model.joblib"))
    with open(os.path.join(out_dir, "card.json"), "w") as f:
        json.dump(asdict(card), f, indent=2)
    return out_dir


def load_model(name: str, version: str, *, root: str = REGISTRY_ROOT_DEFAULT):
    out_dir = _version_dir(name, version, root)
    model_path = os.path.join(out_dir, "model.joblib")
    if not os.path.exists(model_path):
        raise ModelRegistryError(f"no model registered at {name!r}/{version!r} under {root!r}")
    return joblib.load(model_path)


def load_card(name: str, version: str, *, root: str = REGISTRY_ROOT_DEFAULT) -> ModelCard:
    out_dir = _version_dir(name, version, root)
    card_path = os.path.join(out_dir, "card.json")
    if not os.path.exists(card_path):
        raise ModelRegistryError(f"no model card at {name!r}/{version!r} under {root!r}")
    with open(card_path) as f:
        return ModelCard(**json.load(f))


def list_models(*, root: str = REGISTRY_ROOT_DEFAULT) -> list[tuple[str, str]]:
    """[(name, version), ...] for every registered model, empty list
    (not an error) if the registry directory doesn't exist yet."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        name_dir = os.path.join(root, name)
        if not os.path.isdir(name_dir):
            continue
        for version in sorted(os.listdir(name_dir)):
            if os.path.exists(os.path.join(name_dir, version, "model.joblib")):
                out.append((name, version))
    return out
