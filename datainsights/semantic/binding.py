"""
Typed loader for config/bindings/<name>.yaml -- docs/generalization_plan.md
Phase 1. A binding maps one physical schema's entities/columns onto
config/semantic_model.yaml's canonical concepts: renames, value maps,
constants, joins, and bi-temporal column names. See
config/bindings/fdm.yaml for a worked example.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BINDINGS_DIR = REPO_ROOT / "config" / "bindings"
SEMANTIC_MODEL_PATH = REPO_ROOT / "config" / "semantic_model.yaml"


class BitemporalSpec(BaseModel):
    valid_from: str
    valid_to: str


class DerivedField(BaseModel):
    from_: Optional[str] = Field(None, alias="from")
    map: Optional[dict[str, Any]] = None
    default: Optional[Any] = None
    const: Optional[Any] = None

    model_config = ConfigDict(populate_by_name=True)


class JoinSpec(BaseModel):
    entity: str
    on: str
    fields: dict[str, str]
    optional: bool = False


class ConceptBinding(BaseModel):
    entity: Optional[str] = None
    bitemporal: Optional[BitemporalSpec] = None
    fields: dict[str, str] = {}
    derived: dict[str, DerivedField] = {}
    joins: list[JoinSpec] = []
    unavailable: Optional[str] = None


class Binding(BaseModel):
    schema_name: str = Field(alias="schema")
    contract_ref: str
    concepts: dict[str, ConceptBinding]
    # R6: per-schema overrides merged OVER config/rules.yaml by
    # datainsights/runtime.build_runtime -- a schema in another currency
    # or with a different activity profile sets its own thresholds here
    # without touching the global defaults every other schema uses.
    rules: dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)


CONCEPT_KINDS = ("entity", "observation", "event", "version", "valuation")


class FieldSpec(BaseModel):
    type: str
    required: bool = False
    values: Optional[list[Any]] = None


class ConceptSpec(BaseModel):
    """R4: one canonical concept, typed. `kind` says what shape of thing
    it is (a keyed entity, a dated observation, ...) so a domain pack or
    a proposer can reason about it without reading the field list."""
    kind: str
    key: Any  # a field name or a list of field names
    bitemporal: Any = False  # true | false | "optional"
    fields: dict[str, FieldSpec]

    model_config = ConfigDict(extra="forbid")

    @property
    def field_names(self) -> list[str]:
        return list(self.fields)


class SemanticModel(BaseModel):
    concepts: dict[str, ConceptSpec]

    def field_names(self, concept: str) -> list[str]:
        return self.concepts[concept].field_names


def load_binding(path_or_name: str) -> Binding:
    """Accepts a bare name (looked up under config/bindings/) or a path."""
    path = Path(path_or_name)
    if not path.exists():
        path = BINDINGS_DIR / f"{path_or_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No binding at {path_or_name} or {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Binding.model_validate(raw)


def merge_rules(base: dict, override: dict) -> dict:
    """R6: deep merge -- override wins per leaf, base keys the override
    does not name are kept. Neither input is mutated."""
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = merge_rules(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_semantic_model(path: str | None = None) -> SemanticModel:
    path = path or str(SEMANTIC_MODEL_PATH)
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SemanticModel.model_validate(raw)
