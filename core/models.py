"""Serializable BATM domain models.

This module deliberately has no bpy dependency so its behavior can be tested
with the system Python as well as Blender's bundled Python.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, order=True)
class AssetKey:
    library_reference: str
    blend_path: str
    id_type: str
    datablock_name: str

    @property
    def token(self) -> str:
        return "|".join(
            (self.library_reference, self.blend_path, self.id_type, self.datablock_name)
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetKey":
        return cls(
            library_reference=str(value.get("library_reference", "")),
            blend_path=str(value.get("blend_path", "")),
            id_type=str(value.get("id_type", "")).upper(),
            datablock_name=str(value.get("datablock_name", "")),
        )


@dataclass
class AssetSnapshot:
    key: AssetKey
    tags: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    writable: bool = True
    excluded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "tags": list(self.tags),
            "facts": dict(self.facts),
            "fingerprint": dict(self.fingerprint),
            "writable": self.writable,
            "excluded_reason": self.excluded_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetSnapshot":
        return cls(
            key=AssetKey.from_dict(value["key"]),
            tags=[str(tag) for tag in value.get("tags", [])],
            facts=dict(value.get("facts", {})),
            fingerprint=dict(value.get("fingerprint", {})),
            writable=bool(value.get("writable", True)),
            excluded_reason=str(value.get("excluded_reason", "")),
        )


@dataclass
class TagOperation:
    kind: str
    targets: list[str]
    values: list[str] = field(default_factory=list)
    source_value: str = ""
    origin: str = "MANUAL"
    explanation: str = ""
    priority: int = 100
    enabled: bool = True
    operation_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TagOperation":
        return cls(
            operation_id=str(value.get("operation_id") or uuid4()),
            kind=str(value["kind"]).upper(),
            targets=[str(item) for item in value.get("targets", [])],
            values=[str(item) for item in value.get("values", [])],
            source_value=str(value.get("source_value", "")),
            origin=str(value.get("origin", "MANUAL")).upper(),
            explanation=str(value.get("explanation", "")),
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
        )


@dataclass
class DesiredAssetState:
    key: AssetKey
    before: list[str]
    after: list[str]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def changed(self) -> bool:
        return self.enabled and self.before != self.after

    @property
    def valid(self) -> bool:
        return not self.warnings

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["key"] = self.key.to_dict()
        return value


@dataclass
class Rule:
    name: str
    match_field: str = "name_tokens"
    match_values: list[str] = field(default_factory=list)
    add_tags: list[str] = field(default_factory=list)
    match_mode: str = "ANY"
    priority: int = 100
    enabled: bool = True
    rule_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Rule":
        return cls(
            rule_id=str(value.get("rule_id") or uuid4()),
            name=str(value.get("name", "Untitled Rule")),
            match_field=str(value.get("match_field", "name_tokens")),
            match_values=[str(item) for item in value.get("match_values", [])],
            add_tags=[str(item) for item in value.get("add_tags", [])],
            match_mode=str(value.get("match_mode", "ANY")).upper(),
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
        )
