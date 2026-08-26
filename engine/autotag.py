"""AutoTag engine: explainable tag generation from deep asset facts.

Pipeline: enrich facts -> generate library/name tags -> apply rules -> Tag Index
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..core.models import AssetSnapshot, Rule, TagOperation
from .knowledge import knowledge_tags
from .metatags import metatag_operations
from .stemmer import stem as _stem
from .taxonomy import taxonomy_tags

_LIBRARY_STOP_WORDS = {
    "library", "libraries", "lib", "asset", "assets", "blender", "brush", "brushes",
}

_PREFIX_MAP: dict[str, str] = {
    "SM": "StaticMesh", "SK": "SkeletalMesh", "M": "Material", "MI": "MaterialInstance",
    "T": "Texture", "A": "Animation", "BP": "Blueprint", "FX": "VFX",
    "SC": "Sound", "Rig": "Rig", "HDRI": "HDRI", "KIT": "KitPiece", "GN": "GeometryNodes",
}

_PREFIX_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(k) for k in sorted(_PREFIX_MAP, key=len, reverse=True)) + r")_(.+)",
    re.UNICODE,
)

_POLY_THRESHOLDS = {"LOW": 1000, "MEDIUM": 10000}


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


def _poly_count_range(poly_count: int) -> str:
    if poly_count < _POLY_THRESHOLDS["LOW"]:
        return "LOW"
    if poly_count < _POLY_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "HIGH"


def enrich_deep_facts(snapshot: AssetSnapshot) -> None:
    """Add derived facts (library tag, asset name words, deep aggregates)."""
    enrich_basic_facts(snapshot)
    if not snapshot.facts.get("library_tag"):
        snapshot.facts["library_tag"] = library_tag(snapshot.key.library_reference)
    if not snapshot.facts.get("asset_name_words"):
        snapshot.facts["asset_name_words"] = [
            _title_part(token) for token in name_tokens(snapshot.key.datablock_name) if token
        ]
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
    poly_count = snapshot.facts.get("poly_count")
    if poly_count is not None:
        snapshot.facts.setdefault("poly_count_range", _poly_count_range(int(poly_count)))
    snapshot.facts.setdefault("has_animation", bool(snapshot.facts.get("has_animation", False)))
    snapshot.facts.setdefault("has_uvs", bool(snapshot.facts.get("has_uvs", False)))
    snapshot.facts.setdefault("vertex_count", int(snapshot.facts.get("vertex_count", 0)))
    collection_names = snapshot.facts.get("collection_names")
    if collection_names is not None:
        snapshot.facts.setdefault("collection_names", list(collection_names))


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
    if isinstance(value, bool):
        return [str(value).lower()]
    return [str(value).casefold()] if value != "" else []


def rule_matches(snapshot: AssetSnapshot, rule: Rule) -> bool:
    facts = set(_fact_values(snapshot, rule.match_field))
    expected = {str(item).casefold() for item in rule.match_values}
    if rule.match_mode == "ALL":
        if expected.issubset(facts):
            return True
        stems_facts = {_stem(f) for f in facts}
        stems_expected = {_stem(e) for e in expected}
        return stems_expected.issubset(stems_facts)
    if rule.match_mode == "EXACT":
        if facts == expected:
            return True
        return {_stem(f) for f in facts} == {_stem(e) for e in expected}
    if facts & expected:
        return True
    return bool({_stem(f) for f in facts} & {_stem(e) for e in expected})


def _prefix_tags(snapshot: AssetSnapshot) -> list[TagOperation]:
    """Detect naming convention prefixes (SM_, SK_, M_, etc.) and generate tags."""
    ops: list[TagOperation] = []
    name = snapshot.key.datablock_name
    match = _PREFIX_PATTERN.match(name)
    if not match:
        return ops
    prefix, _base_name = match.group(1), match.group(2)
    tag = _PREFIX_MAP.get(prefix)
    if tag:
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[snapshot.key.token],
                values=[tag],
                origin="AUTO",
                priority=7,
                explanation=f"Name prefix: {prefix}_",
            )
        )
    return ops


def _base_tags(snapshot: AssetSnapshot) -> list[TagOperation]:
    """Tags always derived from identity and deep content, no rules needed."""
    ops: list[TagOperation] = []
    target = snapshot.key.token
    lib_tag = snapshot.facts.get("library_tag", "")
    if lib_tag:
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[lib_tag],
                origin="AUTO", priority=5,
                explanation=f"Library: {snapshot.key.library_reference}",
            )
        )
    for word in snapshot.facts.get("asset_name_words", []):
        if not word:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[word],
                origin="AUTO", priority=8,
                explanation=f"Asset name: {snapshot.key.datablock_name}",
            )
        )
    for obj_type in snapshot.facts.get("object_types", []) or []:
        if not obj_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[obj_type.title()],
                origin="AUTO", priority=15,
                explanation=f"Object type: {obj_type}",
            )
        )
    for mod_type in snapshot.facts.get("modifier_types", []) or []:
        if not mod_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[mod_type.title()],
                origin="AUTO", priority=20,
                explanation=f"Modifier: {mod_type}",
            )
        )
    for rig_type in snapshot.facts.get("rig_types", []) or []:
        if not rig_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[rig_type.title()],
                origin="AUTO", priority=22,
                explanation=f"Rig: {rig_type}",
            )
        )
    for node_name in snapshot.facts.get("geometry_node_names", []) or []:
        if not node_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[node_name.title()],
                origin="AUTO", priority=25,
                explanation=f"Geometry Nodes: {node_name}",
            )
        )
    for material_name in (snapshot.facts.get("material_names", []) or [])[:8]:
        if not material_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[material_name.title()],
                origin="AUTO", priority=30,
                explanation=f"Material: {material_name}",
            )
        )
    for coll_name in (snapshot.facts.get("collection_names", []) or [])[:4]:
        if not coll_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[coll_name],
                origin="AUTO", priority=18,
                explanation=f"Collection: {coll_name}",
            )
        )
    poly_range = snapshot.facts.get("poly_count_range", "")
    if poly_range:
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[f"{poly_range}Poly"],
                origin="AUTO", priority=32,
                explanation=f"Poly count range: {poly_range}",
            )
        )
    if snapshot.facts.get("has_animation"):
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=["Animated"],
                origin="AUTO", priority=33,
                explanation="Has animation data",
            )
        )
    return ops


def _deduplicate_operations(ops: list[TagOperation]) -> list[TagOperation]:
    """Merge explanations when multiple sources produce the same (target, value) pair."""
    by_key: dict[tuple[str, str], TagOperation] = {}
    for op in ops:
        for value in op.values:
            key = (op.targets[0] if op.targets else "", value)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = TagOperation(
                    kind=op.kind, targets=list(op.targets), values=[value],
                    source_value=op.source_value, origin=op.origin,
                    explanation=op.explanation, priority=op.priority,
                    enabled=op.enabled, verbatim=op.verbatim,
                )
            else:
                if op.priority < existing.priority:
                    existing.priority = op.priority
                if op.explanation and op.explanation not in existing.explanation:
                    existing.explanation += f"; {op.explanation}"
    return list(by_key.values())


def build_autotag_operations(
    snapshots: list[AssetSnapshot],
    rules: list[Rule],
    knowledge: list[dict] | None = None,
    taxonomy: dict | None = None,
    tax_options: dict | None = None,
) -> list[TagOperation]:
    groups = knowledge or []
    opts = dict(tax_options or {})
    catalog_mode = str(opts.get("catalog_mode", "FULL"))
    cross_tagging = bool(opts.get("cross_tagging", True))
    all_operations: list[TagOperation] = []
    for snapshot in snapshots:
        enrich_deep_facts(snapshot)
        if not snapshot.writable:
            continue
        per_asset: list[TagOperation] = []
        per_asset.extend(_base_tags(snapshot))
        per_asset.extend(metatag_operations(snapshot))
        per_asset.extend(_prefix_tags(snapshot))
        for tag, matched_word in knowledge_tags(snapshot, groups):
            per_asset.append(
                TagOperation(
                    kind="ADD", targets=[snapshot.key.token], values=[tag],
                    origin="AUTO", priority=12,
                    explanation=f"Knowledge: {tag} (matched '{matched_word}')",
                )
            )
        if taxonomy:
            for tag, reason in taxonomy_tags(snapshot, taxonomy, catalog_mode, cross_tagging):
                per_asset.append(
                    TagOperation(
                        kind="ADD", targets=[snapshot.key.token], values=[tag],
                        origin="AUTO", priority=11, explanation=reason,
                    )
                )
        for rule in sorted((item for item in rules if item.enabled), key=lambda item: item.priority):
            if rule_matches(snapshot, rule):
                per_asset.append(
                    TagOperation(
                        kind="ADD", targets=[snapshot.key.token],
                        values=list(rule.add_tags), origin="AUTO",
                        priority=rule.priority,
                        explanation=f"AutoTag rule: {rule.name}",
                    )
                )
        all_operations.extend(_deduplicate_operations(per_asset))
    return all_operations
