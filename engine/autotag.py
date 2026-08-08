"""AutoTag engine: explainable tag generation from deep asset facts.

Pipeline: enrich facts -> generate library/name tags -> apply rules -> Tag Index
"""

from __future__ import annotations

import re
from pathlib import Path
from collections import Counter

from ..core.models import AssetSnapshot, Rule, TagOperation

# Common suffixes/prefixes stripped from library folder names to produce a
# readable tag (e.g. "Comedy_island_library" -> "Comedy Island").
_LIBRARY_STOP_WORDS = {
    "library",
    "libraries",
    "lib",
    "asset",
    "assets",
    "blender",
    "brush",
    "brushes",
}


def name_tokens(name: str) -> list[str]:
    expanded = re.sub(r"[_-]+", " ", name)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", expanded)
    expanded = re.sub(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", expanded)
    return [token.casefold() for token in re.split(r"[^\w]+", expanded, flags=re.UNICODE) if token]


def _title_part(part: str) -> str:
    return part[:1].upper() + part[1:] if part else ""


def library_tag(reference: str) -> str:
    """Normalize a library reference into a Title Case tag."""
    if not reference:
        return ""
    tokens = [
        token
        for token in name_tokens(reference)
        if token.casefold() not in _LIBRARY_STOP_WORDS and len(token) > 1
    ]
    return " ".join(_title_part(token) for token in tokens) if tokens else ""


def enrich_deep_facts(snapshot: AssetSnapshot) -> None:
    """Add derived facts (library tag, asset name words, deep aggregates)."""
    enrich_basic_facts(snapshot)
    if not snapshot.facts.get("library_tag"):
        snapshot.facts["library_tag"] = library_tag(snapshot.key.library_reference)
    if not snapshot.facts.get("asset_name_words"):
        snapshot.facts["asset_name_words"] = [
            _title_part(token) for token in name_tokens(snapshot.key.datablock_name) if token
        ]
    # Aggregate counts derived from deep extractor facts when present.
    objects = snapshot.facts.get("objects", []) or []
    modifiers = snapshot.facts.get("modifiers", []) or []
    materials = snapshot.facts.get("materials", []) or []
    armatures = snapshot.facts.get("armatures", []) or []
    geometry_nodes = snapshot.facts.get("geometry_nodes", []) or []
    snapshot.facts.setdefault("object_types", sorted({str(item.get("type", "")) for item in objects if item.get("type")}))
    snapshot.facts.setdefault("object_names", [str(item.get("name", "")) for item in objects if item.get("name")])
    snapshot.facts.setdefault("modifier_types", sorted({str(item.get("type", "")) for item in modifiers if item.get("type")}))
    snapshot.facts.setdefault("material_names", [str(item.get("name", "")) for item in materials if item.get("name")])
    snapshot.facts.setdefault("rig_types", sorted({str(item.get("rig_type", "")) for item in armatures if item.get("rig_type")}))
    snapshot.facts.setdefault("geometry_node_names", [str(item.get("name", "")) for item in geometry_nodes if item.get("name")])
    snapshot.facts.setdefault("collection_object_count", len(objects))


def enrich_basic_facts(snapshot: AssetSnapshot) -> None:
    snapshot.facts.setdefault("name_tokens", name_tokens(snapshot.key.datablock_name))
    snapshot.facts.setdefault("id_type", snapshot.key.id_type.upper())
    snapshot.facts.setdefault("library_reference", snapshot.key.library_reference)
    snapshot.facts.setdefault(
        "path_tokens",
        [token for part in Path(snapshot.key.blend_path).parts for token in name_tokens(part)],
    )


def _fact_values(snapshot: AssetSnapshot, field: str) -> list[str]:
    value = snapshot.facts.get(field, "")
    if isinstance(value, (list, tuple, set)):
        return [str(item).casefold() for item in value]
    return [str(value).casefold()] if value != "" else []


def rule_matches(snapshot: AssetSnapshot, rule: Rule) -> bool:
    facts = set(_fact_values(snapshot, rule.match_field))
    expected = {str(item).casefold() for item in rule.match_values}
    if rule.match_mode == "ALL":
        return expected.issubset(facts)
    if rule.match_mode == "EXACT":
        return facts == expected
    return bool(facts & expected)


def _base_tags(snapshot: AssetSnapshot) -> list[TagOperation]:
    """Tags always derived from identity and deep content, no rules needed."""
    ops: list[TagOperation] = []
    target = snapshot.key.token
    library_tag = snapshot.facts.get("library_tag", "")
    if library_tag and snapshot.facts.get("include_library_tag", True):
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[library_tag],
                origin="AUTO",
                priority=5,
                explanation=f"Library: {snapshot.key.library_reference}",
            )
        )
    # Asset name words become individual tags (e.g. Environment_OakTree -> Environment, OakTree -> Oak, Tree).
    for word in snapshot.facts.get("asset_name_words", []):
        if not word:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[word],
                origin="AUTO",
                priority=8,
                explanation=f"Asset name: {snapshot.key.datablock_name}",
            )
        )
    # Object type tags for the asset itself.
    for obj_type in snapshot.facts.get("object_types", []) or []:
        if not obj_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[obj_type.title()],
                origin="AUTO",
                priority=15,
                explanation=f"Object type: {obj_type}",
            )
        )
    # Modifier type tags.
    for mod_type in snapshot.facts.get("modifier_types", []) or []:
        if not mod_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[mod_type.title()],
                origin="AUTO",
                priority=20,
                explanation=f"Modifier: {mod_type}",
            )
        )
    # Rig type tags (Rigify, Armature, Mechanical Rig).
    for rig_type in snapshot.facts.get("rig_types", []) or []:
        if not rig_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[rig_type.title()],
                origin="AUTO",
                priority=22,
                explanation=f"Rig: {rig_type}",
            )
        )
    # Geometry node group names.
    for node_name in snapshot.facts.get("geometry_node_names", []) or []:
        if not node_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[node_name.title()],
                origin="AUTO",
                priority=25,
                explanation=f"Geometry Nodes: {node_name}",
            )
        )
    # Material names (first few to avoid tag flooding).
    for material_name in (snapshot.facts.get("material_names", []) or [])[:8]:
        if not material_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[material_name.title()],
                origin="AUTO",
                priority=30,
                explanation=f"Material: {material_name}",
            )
        )
    return ops


def build_autotag_operations(
    snapshots: list[AssetSnapshot], rules: list[Rule]
) -> list[TagOperation]:
    operations: list[TagOperation] = []
    for snapshot in snapshots:
        enrich_deep_facts(snapshot)
        if not snapshot.writable:
            continue
        operations.extend(_base_tags(snapshot))
        for rule in sorted((item for item in rules if item.enabled), key=lambda item: item.priority):
            if rule_matches(snapshot, rule):
                operations.append(
                    TagOperation(
                        kind="ADD",
                        targets=[snapshot.key.token],
                        values=list(rule.add_tags),
                        origin="AUTO",
                        priority=rule.priority,
                        explanation=f"AutoTag rule: {rule.name}",
                    )
                )
    return operations


def build_tag_index(snapshots: list[AssetSnapshot]) -> dict[str, int]:
    """Tag frequency index ``casefold -> count`` over the given snapshots.

    Used by the Manual Tag Editor and Diagnostics; never queries Blender.
    """
    counter: Counter[str] = Counter()
    for snapshot in snapshots:
        seen: set[str] = set()
        for tag in snapshot.tags:
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            counter[key] += 1
    return dict(sorted(counter.items()))