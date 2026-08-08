"""Specialized asset content extractors used by both the worker and local analysis.

Each extractor returns only public, serializable facts. No extractor mutates
Blender data. This module is importable from the background worker and from
the interactive add-on (facts are plain dicts, not bpy pointers).
"""

from __future__ import annotations

from typing import Any


def extract_asset(datablock: Any, library_reference: str = "") -> dict[str, Any]:
    """Top-level identity facts for any asset datablock."""
    identifier = str(getattr(datablock, "bl_rna", None).identifier if getattr(datablock, "bl_rna", None) else "").upper()
    return {
        "id_type": identifier or str(getattr(datablock, "name", "")),
        "name": str(getattr(datablock, "name", "")),
        "library_reference": library_reference,
    }


def extract_objects(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per object for Object and Collection assets."""
    objects: list[Any] = []
    identifier = str(getattr(getattr(datablock, "bl_rna", None), "identifier", "")).upper()
    if identifier == "OBJECT":
        objects = [datablock]
    elif identifier == "COLLECTION":
        objects = list(getattr(datablock, "all_objects", []) or [])
    output: list[dict[str, Any]] = []
    for obj in objects:
        item: dict[str, Any] = {
            "name": str(getattr(obj, "name", "")),
            "type": str(getattr(obj, "type", "")),
        }
        data = getattr(obj, "data", None)
        if data is not None:
            item["data_type"] = str(getattr(getattr(data, "bl_rna", None), "identifier", "")).upper()
        output.append(item)
    return output


def extract_modifiers(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per modifier across all objects in the asset."""
    modifiers: list[dict[str, Any]] = []
    objects = extract_objects(datablock)
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
    objects = extract_objects(datablock)
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


def extract_armatures(datablock: Any) -> list[dict[str, Any]]:
    """Return one fact dict per armature and rig component in the asset."""
    armatures: list[dict[str, Any]] = []
    objects = extract_objects(datablock)
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


def extract_all(datablock: Any, library_reference: str = "") -> dict[str, Any]:
    """Run every extractor and merge results into one facts dict."""
    facts: dict[str, Any] = extract_asset(datablock, library_reference)
    facts["objects"] = extract_objects(datablock)
    facts["modifiers"] = extract_modifiers(datablock)
    facts["materials"] = extract_materials(datablock)
    facts["armatures"] = extract_armatures(datablock)
    facts["geometry_nodes"] = extract_geometry_nodes(datablock)
    # Convenience aggregates used by inference rules.
    facts["object_types"] = sorted({item["type"] for item in facts["objects"]})
    facts["object_names"] = [item["name"] for item in facts["objects"]]
    facts["modifier_types"] = sorted({item["type"] for item in facts["modifiers"]})
    facts["material_names"] = [item["name"] for item in facts["materials"]]
    facts["collection_object_count"] = len(facts["objects"])
    return facts