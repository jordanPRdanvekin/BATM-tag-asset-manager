"""AutoTag engine: explainable tag generation from deep asset facts.

Pipeline: enrich facts -> generate library/name tags -> apply rules -> Tag Index
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..core.models import AssetSnapshot, Rule, TagOperation
from ..core.sanitizer import apply_casing as _style_tag
from .knowledge import knowledge_tags
from .lemma import lemmatize_token as _lemmatize
from .metatags import metatag_operations
from .stemmer import stem as _stem
from .taxonomy import taxonomy_tags
from .segmenter import decompose as _decompose_word, load_compound_splits as _load_fixups

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

# Tags whose presence marks an asset as faunal for the centralized mutex: an
# animal asset must never receive the Human tag, whatever its name says.
_FAUNA_TAGS = {
    "Fauna", "Bird", "Mammal", "Reptile", "Insect", "Aquatic", "Pet",
    "Creature", "Animal", "Canine", "Feline", "Equine", "Bovine", "Arachnid",
    "Arthropod", "Amphibian",
}

_CATALOG_MAP: dict[str, str] = {
    "Fauna": "Assets/Nature/Fauna",
    "Bird": "Assets/Nature/Fauna",
    "Mammal": "Assets/Nature/Fauna",
    "Aquatic": "Assets/Nature/Fauna",
    "Reptile": "Assets/Nature/Fauna",
    "Insect": "Assets/Nature/Fauna",
    "Pet": "Assets/Nature/Fauna",
    "Nature": "Assets/Nature/Terrain",
    "Tree": "Assets/Nature/Tree",
    "Plant": "Assets/Nature/Plant",
    "Flower": "Assets/Nature/Flower",
    "Rock": "Assets/Nature/Rock",
    "Water": "Assets/Nature/Water",
    "Sky": "Assets/Nature/Sky",
    "Terrain": "Assets/Nature/Terrain",
    "Architecture": "Assets/Architecture/Building",
    "Building": "Assets/Architecture/Building",
    "Structural": "Assets/Architecture/Building",
    "Urban": "Assets/Architecture/Urban",
    "Props": "Assets/Props/General",
    "Container": "Assets/Props/Container",
    "Tool": "Assets/Props/Tool",
    "Electronic": "Assets/Props/Electronic",
    "Food": "Assets/Props/Food",
    "Paper": "Assets/Props/Paper",
    "Furniture": "Assets/Furniture/General",
    "Seating": "Assets/Furniture/Seating",
    "Tables": "Assets/Furniture/Tables",
    "Storage": "Assets/Furniture/Storage",
    "Bedding": "Assets/Furniture/Bedding",
    "Decor": "Assets/Furniture/Decor",
    "Human": "Assets/Characters/Human",
    "Humanoid": "Assets/Characters/Humanoid",
    "Robot": "Assets/Characters/Robot",
    "Creature": "Assets/Characters/Creature",
    "Vehicles": "Assets/Vehicles/Car",
    "Car": "Assets/Vehicles/Car",
    "LandVehicle": "Assets/Vehicles/Land",
    "Aircraft": "Assets/Vehicles/Aircraft",
    "Watercraft": "Assets/Vehicles/Watercraft",
    "Combat": "Assets/Combat/Melee",
    "Melee": "Assets/Combat/Melee",
    "Ranged": "Assets/Combat/Ranged",
    "Armor": "Assets/Combat/Armor",
    "Fantasy": "Assets/Fantasy/Mythical",
    "Mythical": "Assets/Fantasy/Mythical",
    "Undead": "Assets/Fantasy/Undead",
    "Arcane": "Assets/Fantasy/Arcane",
    "Sci-Fi": "Assets/SciFi/Spacecraft",
    "Cyborg": "Assets/SciFi/Cyborg",
    "Spacecraft": "Assets/SciFi/Spacecraft",
    "Hologram": "Assets/SciFi/Hologram",
    "VFX": "Assets/VFX/Elemental",
    "Elemental": "Assets/VFX/Elemental",
    "Magical": "Assets/VFX/Magical",
    "Impact": "Assets/VFX/Impact",
    "Animation": "Assets/Animation/Locomotion",
    "Locomotion": "Assets/Animation/Locomotion",
}


def name_tokens(name: str) -> list[str]:
    expanded = re.sub(r"[_-]+", " ", name)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", expanded)
    expanded = re.sub(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", expanded)
    return [token.casefold() for token in re.split(r"[^\w]+", expanded, flags=re.UNICODE) if token]


def _title_part(part: str) -> str:
    return part[:1].upper() + part[1:] if part else ""


def _decorate_tag(tag: str, style: str) -> str:
    """Apply the configured tag naming policy.

    TITLE/NONE are identity: AutoTag already emits curated Title Case and the
    historic preset must stay byte-identical. Other policies (SNEAK, KEBAB,
    PASCAL, LOWER, ...) normalize generated tags via the Sanitizer SSOT.
    """
    style = str(style or "TITLE").upper()
    if style in ("TITLE", "NONE"):
        return tag
    return _style_tag(tag, style)


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
    if not snapshot.facts.get("asset_name_parts"):
        # Atomic components of glued compound names (rosarojavioleta -> rosa,
        # roja, violeta) via the curated exact-match thesaurus. Only applied on
        # exact fixup hits; never guessed by the engine.
        try:
            _fixups = _load_fixups()
            _parts: list[str] = []
            for _token in name_tokens(snapshot.key.datablock_name):
                _words, _was_split = _decompose_word(_token, frozenset(), fixups=_fixups)
                if _was_split:
                    for _w in _words:
                        _w = _w.casefold()
                        if _w and _w not in _parts:
                            _parts.append(_w)
            snapshot.facts["asset_name_parts"] = [_title_part(w) for w in _parts]
        except Exception:
            snapshot.facts["asset_name_parts"] = []
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
    # Deep facts with safe defaults (from extractors) if not present
    # Try to enrich via extract_deep_facts offline inference when bpy not present
    try:
        from .extractors import extract_deep_facts as _extract_deep

        # If facts already contain deep keys, extract_deep_facts will not overwrite via setdefault logic inside
        # Use None obj to attempt offline inference from existing facts dict
        enriched = _extract_deep(None, dict(snapshot.facts))
        for k, v in enriched.items():
            if k not in snapshot.facts:
                snapshot.facts[k] = v
    except Exception:
        pass
    # Ensure all deep extractor keys have safe defaults
    snapshot.facts.setdefault("has_tail", bool(snapshot.facts.get("has_tail", False)))
    snapshot.facts.setdefault("bone_count", int(snapshot.facts.get("bone_count", 0)))
    snapshot.facts.setdefault("has_facial_bones", bool(snapshot.facts.get("has_facial_bones", False)))
    snapshot.facts.setdefault("rig_topology", str(snapshot.facts.get("rig_topology", "unknown")))
    snapshot.facts.setdefault("has_shape_keys", bool(snapshot.facts.get("has_shape_keys", False)))
    snapshot.facts.setdefault("shape_key_count", int(snapshot.facts.get("shape_key_count", 0)))
    snapshot.facts.setdefault("has_facs", bool(snapshot.facts.get("has_facs", False)))
    snapshot.facts.setdefault("facs_keys", list(snapshot.facts.get("facs_keys", []) or []))
    snapshot.facts.setdefault("has_nla", bool(snapshot.facts.get("has_nla", False)))
    snapshot.facts.setdefault("action_names", list(snapshot.facts.get("action_names", []) or []))
    snapshot.facts.setdefault("is_layered_action", bool(snapshot.facts.get("is_layered_action", False)))
    snapshot.facts.setdefault("bbox_volume", float(snapshot.facts.get("bbox_volume", 0.0) or 0.0))
    snapshot.facts.setdefault("bbox_height", float(snapshot.facts.get("bbox_height", 0.0) or 0.0))
    snapshot.facts.setdefault("scale_class", str(snapshot.facts.get("scale_class", "medium")))
    # Clamp scale_class to allowed values
    if snapshot.facts.get("scale_class") not in ("micro", "small", "medium", "large", "hero"):
        snapshot.facts["scale_class"] = "medium"
    snapshot.facts.setdefault("has_subsurface", bool(snapshot.facts.get("has_subsurface", False)))
    snapshot.facts.setdefault("has_metallic", bool(snapshot.facts.get("has_metallic", False)))
    snapshot.facts.setdefault("has_emission", bool(snapshot.facts.get("has_emission", False)))
    snapshot.facts.setdefault("shader_type", str(snapshot.facts.get("shader_type", "unknown")))
    if snapshot.facts.get("shader_type") not in ("skin", "metal", "emissive", "standard", "unknown"):
        snapshot.facts["shader_type"] = "unknown"


def enrich_basic_facts(snapshot: AssetSnapshot) -> None:
    snapshot.facts.setdefault("name_tokens", name_tokens(snapshot.key.datablock_name))
    snapshot.facts.setdefault("id_type", snapshot.key.id_type.upper())
    snapshot.facts.setdefault("library_reference", snapshot.key.library_reference)
    snapshot.facts.setdefault(
        "path_tokens",
        [token for part in Path(snapshot.key.blend_path).parts for token in name_tokens(part)],
    )


def _catalog_tags(snapshot: AssetSnapshot) -> list[TagOperation]:
    """Infer catalog path from top taxonomy tag.

    Returns a TagOperation at priority 4. Example: tag "Fauna" -> catalog "Assets/Nature/Fauna",
    "Architecture" -> "Assets/Architecture/Building".
    """
    # Determine top taxonomy tag: look at enriched facts that may contain taxonomy inference,
    # or fallback to object_types / name_tokens
    top_tag: str | None = None
    # Check explicit facts first
    for key in ("top_taxonomy_tag", "taxonomy_top_tag", "primary_taxonomy_tag"):
        val = snapshot.facts.get(key)
        if isinstance(val, str) and val.strip():
            top_tag = val.strip()
            break
        if isinstance(val, (list, tuple)) and val:
            top_tag = str(val[0]).strip()
            break
    # Check taxonomy_tags via candidates? Use most frequent domain/category from facts
    if not top_tag:
        # Look at object_types or material_names etc for domain hints is not sufficient;
        # instead infer from name_tokens via a quick taxonomy lookup if available
        # For now try to use the first taxonomy-related tag from facts
        for key in ("taxonomy_tags", "inferred_tags", "catalog_tags"):
            vals = snapshot.facts.get(key)
            if isinstance(vals, list) and vals:
                # Could be list of tags or list of (tag,reason)
                first = vals[0]
                if isinstance(first, (list, tuple)):
                    top_tag = str(first[0]).strip()
                else:
                    top_tag = str(first).strip()
                if top_tag:
                    break
    if not top_tag:
        # Fallback: use first of name-based taxonomy inference by scanning name_tokens against map
        name_toks = snapshot.facts.get("name_tokens", []) or []
        for tok in name_toks:
            cap = tok.title() if isinstance(tok, str) else str(tok).title()
            if cap in _CATALOG_MAP:
                top_tag = cap
                break
            # case-insensitive match
            for k in _CATALOG_MAP:
                if k.casefold() == str(tok).casefold():
                    top_tag = k
                    break
            if top_tag:
                break
    if not top_tag:
        # Try object_types mapping: MESH -> not catalog, but Architecture objects?
        obj_types = snapshot.facts.get("object_types", []) or []
        # Not reliable; leave empty if no taxonomy signal
        return []
    # Resolve catalog path
    catalog_path = _CATALOG_MAP.get(top_tag)
    if catalog_path is None:
        # Case-insensitive lookup
        for k, v in _CATALOG_MAP.items():
            if k.casefold() == top_tag.casefold():
                catalog_path = v
                break
    if catalog_path is None:
        # Fallback generic catalog: Assets/<TopTag>
        catalog_path = f"Assets/{top_tag}"
    return [
        TagOperation(
            kind="ADD",
            targets=[snapshot.key.token],
            values=[catalog_path],
            origin="AUTO",
            priority=4,
            explanation=f"Catalog: {catalog_path} (inferred from '{top_tag}')",
        )
    ]


def _fact_values(snapshot: AssetSnapshot, field: str) -> list[str]:
    value = snapshot.facts.get(field, "")
    if isinstance(value, (list, tuple, set)):
        return [str(item).casefold() for item in value]
    if isinstance(value, bool):
        return [str(value).lower()]
    return [str(value).casefold()] if value != "" else []


def _match_normalized(mode: str, expected: set[str], facts: set[str]) -> bool:
    """Normalize both sides through the lemma dictionary, then Porter stem.

    The lemma map preserves technical vocabulary the stemmer would mutilate
    (``procedural`` stays ``procedural``, not ``procedur``); stemming remains as
    a fallback so historic rules keep matching derived forms (``running`` ->
    ``run``).
    """
    facts_lemmas = {_lemmatize(w) for w in facts}
    expected_lemmas = {_lemmatize(w) for w in expected}
    facts_stems = {_stem(w) for w in facts}
    expected_stems = {_stem(w) for w in expected}
    if mode == "ALL":
        return expected_lemmas.issubset(facts_lemmas) or expected_stems.issubset(facts_stems)
    if mode == "EXACT":
        return facts_lemmas == expected_lemmas or facts_stems == expected_stems
    return bool(facts_lemmas & expected_lemmas or facts_stems & expected_stems)


def rule_matches(snapshot: AssetSnapshot, rule: Rule) -> bool:
    facts = set(_fact_values(snapshot, rule.match_field))
    expected = {str(item).casefold() for item in rule.match_values}
    if rule.match_mode == "ALL":
        if expected.issubset(facts):
            return True
        return _match_normalized("ALL", expected, facts)
    if rule.match_mode == "EXACT":
        if facts == expected:
            return True
        return _match_normalized("EXACT", expected, facts)
    if facts & expected:
        return True
    return _match_normalized("ANY", expected, facts)


def _prefix_tags(snapshot: AssetSnapshot, style: str = "TITLE") -> list[TagOperation]:
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
                values=[_decorate_tag(tag, style)],
                origin="AUTO",
                priority=8,
                explanation=f"Name prefix: {prefix}_",
            )
        )
    return ops


def _base_tags(snapshot: AssetSnapshot, style: str = "TITLE") -> list[TagOperation]:
    """Tags always derived from identity and deep content, no rules needed."""
    ops: list[TagOperation] = []
    target = snapshot.key.token
    lib_tag = snapshot.facts.get("library_tag", "")
    if lib_tag:
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(lib_tag, style)],
                origin="AUTO", priority=35,
                explanation=f"Library: {snapshot.key.library_reference}",
            )
        )
    for word in snapshot.facts.get("asset_name_words", []):
        if not word:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(word, style)],
                origin="AUTO", priority=40,
                explanation=f"Asset name: {snapshot.key.datablock_name}",
            )
        )
    # Compound components (rosarojavioleta -> Rosa, Roja, Violeta) alongside the
    # compound unit. Gated by the AutoTag "Split Compound Names" option (default on).
    if snapshot.facts.get("_split_compounds_enabled", True):
        for word in snapshot.facts.get("asset_name_parts", []):
            if not word:
                continue
            ops.append(
                TagOperation(
                    kind="ADD", targets=[target], values=[_decorate_tag(word, style)],
                    origin="AUTO", priority=41,
                    explanation=f"Asset name components: {snapshot.key.datablock_name}",
                )
            )
    for obj_type in snapshot.facts.get("object_types", []) or []:
        if not obj_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(obj_type.title(), style)],
                origin="AUTO", priority=20,
                explanation=f"Object type: {obj_type}",
            )
        )
    for mod_type in snapshot.facts.get("modifier_types", []) or []:
        if not mod_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(mod_type.title(), style)],
                origin="AUTO", priority=22,
                explanation=f"Modifier: {mod_type}",
            )
        )
    for rig_type in snapshot.facts.get("rig_types", []) or []:
        if not rig_type:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(rig_type.title(), style)],
                origin="AUTO", priority=23,
                explanation=f"Rig: {rig_type}",
            )
        )
    # Rig presentation. A rigged asset always carries "Rigged"; the locomotion
    # topology tag (Biped/Quadruped) requires a real armature classification and
    # is never inferred from a raw bone count alone (a single grip bone on a
    # prop is Rigged, not Biped).
    bone_count = int(snapshot.facts.get("bone_count", 0) or 0)
    if bone_count >= 1:
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag("Rigged", style)],
                origin="AUTO", priority=24,
                explanation=f"Rig: {bone_count} bone(s)",
            )
        )
    topology = str(snapshot.facts.get("rig_topology", "")).casefold()
    if topology == "biped":
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag("Biped", style)],
                origin="AUTO", priority=24,
                explanation="Rig topology: biped",
            )
        )
    elif topology == "quadruped":
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag("Quadruped", style)],
                origin="AUTO", priority=24,
                explanation="Rig topology: quadruped",
            )
        )
    for node_name in snapshot.facts.get("geometry_node_names", []) or []:
        if not node_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(node_name.title(), style)],
                origin="AUTO", priority=26,
                explanation=f"Geometry Nodes: {node_name}",
            )
        )
    for material_name in (snapshot.facts.get("material_names", []) or [])[:8]:
        if not material_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(material_name.title(), style)],
                origin="AUTO", priority=27,
                explanation=f"Material: {material_name}",
            )
        )
    for coll_name in (snapshot.facts.get("collection_names", []) or [])[:4]:
        if not coll_name:
            continue
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(coll_name, style)],
                origin="AUTO", priority=21,
                explanation=f"Collection: {coll_name}",
            )
        )
    poly_range = snapshot.facts.get("poly_count_range", "")
    if poly_range:
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag(f"{poly_range}Poly", style)],
                origin="AUTO", priority=28,
                explanation=f"Poly count range: {poly_range}",
            )
        )
    if snapshot.facts.get("has_animation"):
        ops.append(
            TagOperation(
                kind="ADD", targets=[target], values=[_decorate_tag("Animated", style)],
                origin="AUTO", priority=29,
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
    split_compounds = bool(opts.get("split_compounds", True))
    tag_style = str(opts.get("tag_style", "TITLE")).upper()
    all_operations: list[TagOperation] = []
    for snapshot in snapshots:
        enrich_deep_facts(snapshot)
        snapshot.facts["_split_compounds_enabled"] = split_compounds
        if not snapshot.writable:
            continue
        per_asset: list[TagOperation] = []
        per_asset.extend(_base_tags(snapshot, tag_style))
        per_asset.extend(metatag_operations(snapshot))
        per_asset.extend(_prefix_tags(snapshot, tag_style))
        # Catalog inference at priority 4 (before other tags)
        try:
            per_asset.extend(_catalog_tags(snapshot))
        except Exception:
            pass
        for tag, matched_word in knowledge_tags(snapshot, groups):
            per_asset.append(
                TagOperation(
                    kind="ADD", targets=[snapshot.key.token], values=[tag],
                    origin="AUTO", priority=7,
                    explanation=f"Knowledge: {tag} (matched '{matched_word}')",
                )
            )
        if taxonomy:
            for tag, reason in taxonomy_tags(snapshot, taxonomy, catalog_mode, cross_tagging):
                per_asset.append(
                    TagOperation(
                        kind="ADD", targets=[snapshot.key.token], values=[tag],
                        origin="AUTO", priority=6, explanation=reason,
                    )
                )
            # Catalog inference may also be driven by taxonomy top result when not already inferred
            # If _catalog_tags returned empty but taxonomy produced tags, retry with taxonomy top
            has_catalog = any("Assets/" in v for op in per_asset for v in op.values)
            if not has_catalog:
                try:
                    tax_tags = taxonomy_tags(snapshot, taxonomy, catalog_mode, cross_tagging)
                    if tax_tags:
                        top = tax_tags[0][0]
                        # Temporarily set fact for catalog inference
                        snapshot.facts["top_taxonomy_tag"] = top
                        per_asset.extend(_catalog_tags(snapshot))
                except Exception:
                    pass
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
        # Centralized Fauna/Human mutex. Evidence: taxonomy Fauna domain or a
        # Bird-family alias (grulla/crane included) OR a knowledge group tagged
        # with a faunal concept. When faunal, every proposed Human tag is
        # dropped — an animal asset is never Human, whatever its name contains.
        faunal = snapshot.facts.get("faunal_evidence", False)
        try:
            from .taxonomy import is_faunal as _is_faunal
            faunal = faunal or _is_faunal(snapshot, taxonomy)
        except Exception:
            pass
        if groups and not faunal:
            try:
                faunal = any(
                    tag.casefold() in _FAUNA_TAGS for tag, _ in knowledge_tags(snapshot, groups)
                )
            except Exception:
                pass
        snapshot.facts["faunal_evidence"] = bool(faunal)
        if faunal:
            per_asset = [
                op for op in per_asset
                if not any(str(value).casefold() == "human" for value in op.values)
            ]
        all_operations.extend(_deduplicate_operations(per_asset))
    return all_operations
