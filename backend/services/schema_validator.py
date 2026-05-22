"""JSON Schema validation for AgentMessage envelopes and routing rules."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from functools import lru_cache

from jsonschema import Draft202012Validator, ValidationError, RefResolver

SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"

SCHEMA_FILES = {
    "reformulated_task":   "reformulated_task.schema.json",
    "plan":                "plan.schema.json",
    "contribution_receipt":"contribution_receipt.schema.json",
    "review_verdict":      "review_verdict.schema.json",
    "routing_rules":       "routing_rules.schema.json",
    "skills_manifest":     "skills_manifest.schema.json",
}


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    if name not in SCHEMA_FILES:
        raise KeyError(f"Unknown schema: {name}. Known: {list(SCHEMA_FILES)}")
    path = SCHEMAS_DIR / SCHEMA_FILES[name]
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def get_validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    return Draft202012Validator(schema)


def validate(name: str, instance: Any) -> list[str]:
    """Return list of error messages (empty if valid)."""
    validator = get_validator(name)
    errors = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: e.absolute_path):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def assert_valid(name: str, instance: Any) -> None:
    errs = validate(name, instance)
    if errs:
        raise ValueError(f"Spec '{name}' invalid:\n" + "\n".join(f"  - {e}" for e in errs))
