"""Specialized asset content extractors used by both the worker and local analysis.

Each extractor returns only public, serializable facts. No extractor mutates
Blender data. This module is importable from the background worker and from
the interactive add-on (facts are plain dicts, not bpy pointers).
"""

from __future__ import annotations

from typing import Any

# Volume caps keep every facts payload bounded no matter how large the asset is,
# protecting both the IPC JSON size and the interactive Current File analysis.
MAX_OBJECTS = 200
MAX_MODIFIERS = 100
MAX_MATERIALS = 64
MAX_ARMATURES = 32
MAX_NODE_GROUPS = 32
MAX_RELATED = 32
MAX_BONES = 200
MAX_CONSTRAINTS = 100


def extract_asset(datablock: Any, library_reference: str = "") -> dict[str, Any]:
    """Top-level identity facts for any asset datablock."""
    identifier = str(getattr(datablock, "bl_rna", None).identifier if getattr(datablock, "bl_rna", None) else "").upper()
    return {
        "id_type": identifier or str(getattr(datablock, "name", "")),
        "name": str(getattr(datablock, "name", "")),
        "library_reference": library_reference,
    }


def extract_objects(datablock: Any, limit: int | None = None) -> list[dict[str, Any]]:
    """Return one fact dict per object for Object and Collection assets."""
    objects: list[Any] = []
    identifier = str(getattr(getattr(datablock, "bl_rna", None), "identifier", "")).upper()
    if identifier == "OBJECT":
        objects = [datablock]
    elif identifier == "COLLECTION":
        objects = list(getattr(datablock, "all_objects", []) or [])
    if limit is not None:
        objects = objects[:limit]
    output: list[dict[str, Any]] = []
    for obj in objects:
        item: dict[str, Any] = {
            "name": str(getattr(obj, "name", "")),
            "type": str(getattr(obj, "type", "")),
        }
        parent = getattr(obj, "parent", None)
        if parent is not None:
            item["parent"] = str(getattr(parent, "name", ""))
        children = [
            str(getattr(child, "name", ""))
            for child in (getattr(obj, "children", None) or [])
        ]
        if children:
            item["children"] = children[:MAX_RELATED]
        data = getattr(obj, "data", None)
        if data is not None:
            item["data_type"] = str(getattr(getattr(data, "bl_rna", None), "identifier", "")).upper()
        output.append(item)
    return output


def extract_modifiers(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per modifier across all objects in the asset."""
    modifiers: list[dict[str, Any]] = []
    objects = extract_objects(datablock, MAX_OBJECTS)
    for obj_fact in objects:
        obj = _resolve_object(datablock, obj_fact["name"])
        if obj is None:
            continue
        for modifier in getattr(obj, "modifiers", []) or []:
            modifiers.append(
                {
                    "name": str(getattr(modifier, "name", "")),
                    "type": str(getattr(modifier, "type", "")),
                    "object": obj_fact["name"],
                }
            )
    return modifiers


def extract_materials(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per material slot on the asset's objects."""
    materials: list[dict[str, Any]] = []
    objects = extract_objects(datablock, MAX_OBJECTS)
    for obj_fact in objects:
        obj = _resolve_object(datablock, obj_fact["name"])
        if obj is None:
            continue
        for slot in getattr(obj, "material_slots", []) or []:
            material = getattr(slot, "material", None)
            if material is not None:
                materials.append(
                    {
                        "name": str(getattr(material, "name", "")),
                        "object": obj_fact["name"],
                    }
                )
    return materials


def extract_constraints(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per object constraint found in the asset."""
    constraints: list[dict[str, Any]] = []
    objects = extract_objects(datablock, MAX_OBJECTS)
    for obj_fact in objects:
        obj = _resolve_object(datablock, obj_fact["name"])
        if obj is None:
            continue
        for constraint in getattr(obj, "constraints", []) or []:
            constraints.append(
                {
                    "name": str(getattr(constraint, "name", "")),
                    "type": str(getattr(constraint, "type", "")),
                    "object": obj_fact["name"],
                }
            )
    return constraints


def extract_armatures(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per armature and rig component in the asset."""
    armatures: list[dict[str, Any]] = []
    objects = extract_objects(datablock, MAX_OBJECTS)
    for obj_fact in objects:
        obj = _resolve_object(datablock, obj_fact["name"])
        if obj is None or obj_fact["type"] != "ARMATURE":
            continue
        armature = getattr(obj, "data", None)
        item: dict[str, Any] = {
            "name": str(getattr(obj, "name", "")),
            "object": obj_fact["name"],
        }
        if armature is not None:
            item["bone_count"] = len(getattr(armature, "bones", []) or [])
            item["bones"] = [
                str(bone.name)
                for bone in (getattr(armature, "bones", []) or [])[:MAX_BONES]
            ]
        # Detect Rigify-style rigs (constraint or bone name markers).
        item["rig_type"] = _detect_rig_type(obj)
        armatures.append(item)
    return armatures


def _detect_rig_type(obj: Any) -> str:
    """Heuristic rig classification using public data only."""
    armature = getattr(obj, "data", None)
    if armature is None:
        return ""
    bone_names = {str(bone.name).casefold() for bone in getattr(armature, "bones", []) or []}
    if any("rigify" in name for name in bone_names) or "Rigify" in str(getattr(obj, "name", "")):
        return "RIGIFY"
    if any("def_" in name or "org_" in name or "mch_" in name for name in bone_names):
        return "MECHANICAL_RIG"
    return "ARMATURE"


def extract_geometry_nodes(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per Geometry Nodes modifier found on the asset."""
    node_groups: list[dict[str, Any]] = []
    for modifier in extract_modifiers(datablock):
        if modifier["type"] == "NODES":
            # Re-resolve the object to read the node group name from the modifier.
            obj = _resolve_object(datablock, modifier["object"])
            if obj is None:
                continue
            found = None
            for mod in getattr(obj, "modifiers", []) or []:
                if getattr(mod, "type", "") == "NODES":
                    found = mod
                    break
            if found is not None:
                node_group = getattr(found, "node_group", None)
                node_groups.append(
                    {
                        "name": str(getattr(node_group, "name", "") if node_group is not None else ""),
                        "modifier": modifier["name"],
                        "object": modifier["object"],
                    }
                )
    return node_groups


def _resolve_object(datablock: Any, name: str) -> Any | None:
    """Resolve an object by name from the datablock's scene/collection context."""
    try:
        for obj in getattr(datablock, "all_objects", []) or []:
            if getattr(obj, "name", "") == name:
                return obj
    except Exception:
        pass
    # Object assets are the datablock itself.
    identifier = str(getattr(getattr(datablock, "bl_rna", None), "identifier", "")).upper()
    if identifier == "OBJECT" and getattr(datablock, "name", "") == name:
        return datablock
    return None


def _max_hierarchy_depth(family_objects: list[dict[str, Any]]) -> int:
    """Maximum parent-chain length within the captured object facts."""
    names = {item.get("name", "") for item in family_objects if item.get("name")}
    by_name = {item["name"]: item for item in family_objects if item.get("name")}
    maximum = 0
    for item in family_objects:
        count = 0
        cursor = item
        seen: set[str] = set()
        while cursor.get("parent") in names and cursor["parent"] not in seen:
            seen.add(cursor["parent"])
            count += 1
            cursor = by_name.get(cursor["parent"], {})
            if not cursor:
                break
        maximum = max(maximum, count)
    return maximum


def extract_all(datablock: Any, library_reference: str = "") -> dict[str, Any]:
    """Run every extractor and merge results into one canonical facts dict.

    The same function feeds the background worker (external .blend files) and
    the interactive add-on (Current File assets), so every asset always yields
    the identical contract: plural aggregates plus deterministic singular
    aliases for rules written against either spelling.
    """
    facts: dict[str, Any] = extract_asset(datablock, library_reference)
    identifier: str = facts["id_type"]
    facts["objects"] = extract_objects(datablock, MAX_OBJECTS)[:MAX_OBJECTS]
    facts["modifiers"] = extract_modifiers(datablock)[:MAX_MODIFIERS]
    facts["materials"] = extract_materials(datablock)[:MAX_MATERIALS]
    facts["armatures"] = extract_armatures(datablock)[:MAX_ARMATURES]
    facts["geometry_nodes"] = extract_geometry_nodes(datablock)[:MAX_NODE_GROUPS]
    facts["constraints"] = extract_constraints(datablock)[:MAX_CONSTRAINTS]
    # Direct datablock assets (not objects/collections) still expose their own
    # identity as material facts so rules like "builtin-shader" can classify
    # material assets, not only object material slots.
    if identifier == "MATERIAL":
        name = facts.get("name", "")
        if name and not any(item.get("name") == name for item in facts["materials"]):
            facts["materials"].append({"name": name, "object": ""})
            facts["materials"] = facts["materials"][:MAX_MATERIALS]
    # Convenience aggregates used by inference rules and the Tag Index.
    facts["object_types"] = sorted({item["type"] for item in facts["objects"]})
    facts["object_names"] = [item["name"] for item in facts["objects"]]
    facts["modifier_types"] = sorted({item["type"] for item in facts["modifiers"]})
    facts["material_names"] = [item["name"] for item in facts["materials"]]
    facts["rig_types"] = sorted({item["rig_type"] for item in facts["armatures"] if item.get("rig_type")})
    facts["geometry_node_names"] = [item["name"] for item in facts["geometry_nodes"] if item.get("name")]
    facts["bone_names"] = [
        bone
        for armature in facts["armatures"]
        for bone in (armature.get("bones", []) or [])
    ][:MAX_BONES]
    facts["constraint_types"] = sorted({item["type"] for item in facts["constraints"]})
    facts["parent_names"] = sorted(
        {item["parent"] for item in facts["objects"] if item.get("parent")}
        | {
            child
            for item in facts["objects"]
            for child in (item.get("children", []) or [])
            if child
        }
    )
    facts["hierarchy_depth"] = _max_hierarchy_depth(facts["objects"])
    facts["collection_object_count"] = len(facts["objects"])
    # Singular aliases: deterministic, only populated when unambiguous.
    types = facts["object_types"]
    facts["object_type"] = types[0] if identifier == "OBJECT" and len(types) == 1 else ""
    facts["collection_types"] = types
    facts["data_type"] = (
        str(facts["objects"][0].get("data_type", ""))
        if identifier == "OBJECT" and len(facts["objects"]) == 1
        else ""
    )
    return facts