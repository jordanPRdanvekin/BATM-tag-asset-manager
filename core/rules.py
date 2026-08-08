"""Versioned declarative rule storage and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Rule

RULE_SCHEMA_VERSION = 1
ALLOWED_FIELDS = {
    "name_tokens",
    "id_type",
    "object_type",
    "data_type",
    "collection_types",
    "library_reference",
    "path_tokens",
    "library_tag",
    "object_names",
    "object_types",
    "modifier_types",
    "material_names",
    "rig_types",
    "geometry_node_names",
    "bone_names",
    "constraint_types",
    "parent_names",
    "hierarchy_depth",
}
ALLOWED_MODES = {"ANY", "ALL", "EXACT"}


def validate_rule(rule: Rule) -> list[str]:
    errors: list[str] = []
    if not rule.name.strip():
        errors.append("Rule name is required")
    if rule.match_field not in ALLOWED_FIELDS:
        errors.append(f"Unsupported match field: {rule.match_field}")
    if rule.match_mode not in ALLOWED_MODES:
        errors.append(f"Unsupported match mode: {rule.match_mode}")
    if not rule.match_values:
        errors.append("At least one match value is required")
    if not rule.add_tags:
        errors.append("At least one output tag is required")
    return errors


def load_rule_payload(payload: dict[str, Any]) -> list[Rule]:
    if int(payload.get("schema_version", 0)) != RULE_SCHEMA_VERSION:
        raise ValueError("Unsupported BATM rule schema")
    rules = [Rule.from_dict(value) for value in payload.get("rules", [])]
    errors = [f"{rule.name}: {error}" for rule in rules for error in validate_rule(rule)]
    if errors:
        raise ValueError("; ".join(errors))
    return sorted(rules, key=lambda rule: rule.priority)


def load_rules(path: str | Path) -> list[Rule]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return load_rule_payload(json.load(handle))


def rules_payload(rules: list[Rule]) -> dict[str, Any]:
    return {"schema_version": RULE_SCHEMA_VERSION, "rules": [rule.to_dict() for rule in rules]}
