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


def extract_mesh_stats(datablock: Any) -> dict[str, Any]:
    """O(1) mesh aggregates: polygon/vertex counts and UV/shape-key presence.

    Uses only ``len()`` against raw datablock collections, which the Blender C
    API resolves in constant time, so high-poly assets never iterate faces in
    Python. Missing/broken data yields safe zero defaults.
    """
    total_poly = 0
    total_verts = 0
    has_uvs = False
    has_basis_shapes = False
    identifier = str(getattr(getattr(datablock, "bl_rna", None), "identifier", "")).upper()

    def _accumulate(mesh: Any) -> None:
        nonlocal total_poly, total_verts, has_uvs, has_basis_shapes
        if mesh is None:
            return
        try:
            total_poly += len(getattr(mesh, "polygons", []) or [])
            total_verts += len(getattr(mesh, "vertices", []) or [])
            if getattr(mesh, "uv_layers", None) is not None:
                has_uvs = has_uvs or bool(len(mesh.uv_layers))
            sk = getattr(mesh, "shape_keys", None)
            if sk is not None and getattr(sk, "key_blocks", None) is not None:
                has_basis_shapes = has_basis_shapes or len(sk.key_blocks) > 1
        except Exception:
            pass

    if identifier == "MESH":
        _accumulate(datablock)
    for obj_fact in extract_objects(datablock, MAX_OBJECTS):
        obj = _resolve_object(datablock, obj_fact["name"])
        if obj is None:
            continue
        try:
            data = getattr(obj, "data", None)
            if str(getattr(getattr(data, "bl_rna", None), "identifier", "")).upper() == "MESH":
                _accumulate(data)
        except Exception:
            continue
    return {
        "poly_count": int(total_poly),
        "vertex_count": int(total_verts),
        "has_uvs": bool(has_uvs),
        "has_shape_keys": bool(has_basis_shapes),
    }


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
    # O(1) mesh aggregates (guarded: raises harmlessly offline without bpy).
    try:
        facts.update(extract_mesh_stats(datablock))
    except Exception:
        facts.setdefault("poly_count", 0)
        facts.setdefault("vertex_count", 0)
        facts.setdefault("has_uvs", False)
    # Deep rig/animation/material facts: topology, shape keys/FACS, NLA,
    # bounding volume and shader inference. Outside bpy this merges only keys
    # already derivable from facts (safe defaults) — inline workers always get
    # the full extraction.
    try:
        facts.update(extract_deep_facts(datablock, facts))
    except Exception:
        pass
    return facts


# ---------------------------------------------------------------------------
# Deep rig / animation / material extractors (bpy-aware with offline fallback)
# ---------------------------------------------------------------------------

_FACIAL_KEYWORDS = ("jaw", "eye", "brow", "mouth", "lip", "nose", "ear", "tongue", "face")

_FACS_LIST: list[str] = [
    "jaw_open",
    "jaw_close",
    "eye_blink",
    "eye_wide",
    "eye_squint",
    "mouth_smile",
    "mouth_frown",
    "mouth_open",
    "mouth_press",
    "brow_raise",
    "brow_lower",
    "brow_furrow",
    "nose_wrinkle",
    "cheek_raise",
    "cheek_puff",
    "lip_pucker",
    "lip_stretch",
    "tongue_out",
    "viseme",
    "viseme_a",
]

_SCALE_THRESHOLDS = (0.3, 0.6, 2.0, 5.0)


def _bone_names_from_obj(obj: Any) -> list[str]:
    """Extract bone names from a bpy armature object or a facts dict fallback."""
    # If obj is a dict acting as facts, look for bone_names / bones.
    if isinstance(obj, dict):
        candidates = obj.get("bone_names") or obj.get("bones") or []
        if isinstance(candidates, (list, tuple, set)):
            return [str(b) for b in candidates]
        return []
    try:
        data = getattr(obj, "data", None)
        if data is not None:
            bones = getattr(data, "bones", None)
            if bones is not None:
                return [str(getattr(b, "name", "")) for b in bones if getattr(b, "name", None) is not None]
        # Fallback: obj itself may be an armature data-block
        bones = getattr(obj, "bones", None)
        if bones is not None:
            return [str(getattr(b, "name", "")) for b in bones if getattr(b, "name", None) is not None]
    except Exception:
        pass
    # Also allow obj to carry a synthetic 'bone_names' attribute (offline tests)
    try:
        synthetic = getattr(obj, "bone_names", None)
        if synthetic:
            return [str(b) for b in synthetic]
    except Exception:
        pass
    return []


def extract_armature_topology(obj: Any) -> dict[str, Any]:
    """Inspect armature bones to classify quadruped vs biped topology.

    Works inside bpy context via obj.data.bones, but also testable offline via
    a facts dict or synthetic bone_names attribute.
    """
    try:
        bone_names = _bone_names_from_obj(obj)
        # Also accept base_facts dict passed as obj
        if not bone_names and isinstance(obj, dict):
            bone_names = [str(b) for b in (obj.get("bone_names") or obj.get("bones") or [])]
        lower = [n.casefold() for n in bone_names]
        bone_count = len(bone_names)
        has_tail = any("tail" in n for n in lower)
        has_facial = any(any(kw in n for kw in _FACIAL_KEYWORDS) for n in lower)
        # Patterns
        has_spine = any("spine" in n for n in lower)
        has_pelvis = any("pelvis" in n for n in lower)
        has_upper = any("upper_arm" in n or "upper arm" in n or "upperarm" in n for n in lower)
        has_lower = any("lower_arm" in n or "lower arm" in n or "lowerarm" in n for n in lower)
        # Also detect generic arm/leg patterns
        has_arm = any("arm" in n for n in lower)
        has_leg = any("leg" in n for n in lower)
        # Topology heuristic
        if (has_spine or has_pelvis) and has_tail:
            topology = "quadruped"
        elif has_upper or has_lower:
            topology = "biped"
        elif has_arm and has_leg and not has_tail:
            topology = "biped"
        elif has_tail:
            topology = "quadruped"
        else:
            topology = "unknown"
        return {
            "rig_topology": topology,
            "has_tail": bool(has_tail),
            "bone_count": int(bone_count),
            "has_facial_bones": bool(has_facial),
        }
    except Exception:
        return {
            "rig_topology": "unknown",
            "has_tail": False,
            "bone_count": 0,
            "has_facial_bones": False,
        }


def extract_shape_keys(obj: Any) -> dict[str, Any]:
    """Inspect shape keys for FACS detection."""
    try:
        key_blocks: list[Any] = []
        # dict fallback
        if isinstance(obj, dict):
            # facts dict may carry shape_keys or shape_key_names
            candidates = obj.get("shape_keys") or obj.get("shape_key_names") or obj.get("shape_key_count")
            if isinstance(candidates, list):
                key_blocks = candidates  # list of names
            elif isinstance(candidates, int):
                return {
                    "has_shape_keys": candidates > 0,
                    "shape_key_count": int(candidates),
                    "has_facs": False,
                    "facs_keys": [],
                }
            else:
                key_blocks = []
            # Normalize to names
            names = [str(k if isinstance(k, str) else getattr(k, "name", "")) for k in key_blocks]
        else:
            try:
                data = getattr(obj, "data", None)
                sk = getattr(data, "shape_keys", None) if data is not None else None
                if sk is None:
                    # Some objects store shape_keys directly on obj
                    sk = getattr(obj, "shape_keys", None)
                if sk is not None:
                    blocks = getattr(sk, "key_blocks", None)
                    if blocks is not None:
                        key_blocks = list(blocks)
                    else:
                        key_blocks = []
                names = [str(getattr(kb, "name", "")) for kb in key_blocks]
            except Exception:
                names = []
        # Filter empty
        names = [n for n in names if n]
        has_shape = len(names) > 0
        count = len(names)
        facs_keys: list[str] = []
        lower_facs = [f.casefold() for f in _FACS_LIST]
        for name in names:
            nl = name.casefold()
            for facs in lower_facs:
                if facs in nl:
                    facs_keys.append(name)
                    break
        # Deduplicate preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for k in facs_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        return {
            "has_shape_keys": bool(has_shape),
            "shape_key_count": int(count),
            "has_facs": bool(len(deduped) > 0),
            "facs_keys": deduped,
        }
    except Exception:
        return {
            "has_shape_keys": False,
            "shape_key_count": 0,
            "has_facs": False,
            "facs_keys": [],
        }


def extract_animation_data(obj: Any) -> dict[str, Any]:
    """Inspect animation_data for NLA, layered actions and action names."""
    try:
        if isinstance(obj, dict):
            # Offline fallback via facts dict
            has_anim = bool(obj.get("has_animation", False) or obj.get("has_nla", False) or obj.get("action_names"))
            has_nla = bool(obj.get("has_nla", False))
            action_names = list(obj.get("action_names") or [])
            is_layered = bool(obj.get("is_layered_action", False))
            # Also check nla_tracks presence
            if not has_nla and obj.get("nla_tracks"):
                has_nla = True
                has_anim = True
            return {
                "has_animation": bool(has_anim),
                "has_nla": bool(has_nla),
                "action_names": [str(a) for a in action_names],
                "is_layered_action": bool(is_layered),
            }
        anim = getattr(obj, "animation_data", None)
        if anim is None:
            # Also check if obj is an Action datablock with slots
            try:
                # Action datablock may have slots attribute (Blender 5.2 layered actions)
                if hasattr(obj, "slots") or hasattr(obj, "layers"):
                    action = obj
                    has_anim = True
                    has_nla = False
                    name = str(getattr(action, "name", ""))
                    is_layered = bool(getattr(action, "slots", None) or getattr(action, "layers", None) or getattr(action, "is_layered", False))
                    return {
                        "has_animation": True,
                        "has_nla": False,
                        "action_names": [name] if name else [],
                        "is_layered_action": bool(is_layered),
                    }
            except Exception:
                pass
            return {
                "has_animation": False,
                "has_nla": False,
                "action_names": [],
                "is_layered_action": False,
            }
        has_anim = False
        has_nla = False
        action_names: list[str] = []
        is_layered = False
        try:
            nla_tracks = getattr(anim, "nla_tracks", None)
            if nla_tracks is not None and len(list(nla_tracks)) > 0:
                has_nla = True
                has_anim = True
        except Exception:
            pass
        try:
            action = getattr(anim, "action", None)
            if action is not None:
                has_anim = True
                name = str(getattr(action, "name", ""))
                if name:
                    action_names.append(name)
                # Blender 5.2 layered actions: action.slots or action.layers
                try:
                    slots = getattr(action, "slots", None)
                    if slots is not None and len(list(slots)) > 0:
                        is_layered = True
                    layers = getattr(action, "layers", None)
                    if layers is not None and len(list(layers)) > 0:
                        is_layered = True
                    if bool(getattr(action, "is_layered", False)):
                        is_layered = True
                except Exception:
                    pass
                # Also check action_slots on animation_data (Blender 5.2)
                try:
                    slots2 = getattr(anim, "action_slots", None)
                    if slots2 is not None and len(list(slots2)) > 0:
                        is_layered = True
                        has_anim = True
                except Exception:
                    pass
        except Exception:
            pass
        # Also check action_slots directly on animation_data
        try:
            if not is_layered:
                slots = getattr(anim, "action_slots", None)
                if slots is not None and len(list(slots)) > 0:
                    is_layered = True
                    has_anim = True
        except Exception:
            pass
        # Tokens from action names (split on non-alnum)
        # Keep raw names; autotag can tokenize later
        # Ensure has_animation also true if action_names present
        if action_names:
            has_anim = True
        return {
            "has_animation": bool(has_anim),
            "has_nla": bool(has_nla),
            "action_names": action_names,
            "is_layered_action": bool(is_layered),
        }
    except Exception:
        return {
            "has_animation": False,
            "has_nla": False,
            "action_names": [],
            "is_layered_action": False,
        }


def extract_bounding_volume(obj: Any) -> dict[str, Any]:
    """Compute bbox volume/height and scale class via foreach_get or dimensions."""
    try:
        if isinstance(obj, dict):
            # Offline fallback via facts dict
            vol = float(obj.get("bbox_volume", 0.0) or 0.0)
            height = float(obj.get("bbox_height", 0.0) or 0.0)
            # If not present but dimensions available
            if vol == 0.0 and obj.get("dimensions"):
                try:
                    dims = list(obj.get("dimensions"))
                    if len(dims) >= 3:
                        vol = float(dims[0]) * float(dims[1]) * float(dims[2])
                        height = float(dims[2])
                except Exception:
                    pass
            max_dim = height
            # Try to infer max dimension from bbox_volume if height missing
            if max_dim == 0.0 and vol > 0:
                # approximate cube root
                max_dim = vol ** (1.0 / 3.0)
            # Also check bbox_corners
            if obj.get("bbox_corners"):
                try:
                    corners = list(obj.get("bbox_corners"))
                    xs = [c[0] for c in corners]
                    ys = [c[1] for c in corners]
                    zs = [c[2] for c in corners]
                    dx = max(xs) - min(xs) if xs else 0
                    dy = max(ys) - min(ys) if ys else 0
                    dz = max(zs) - min(zs) if zs else 0
                    vol = float(dx * dy * dz)
                    height = float(dz)
                    max_dim = max(dx, dy, dz)
                except Exception:
                    pass
            # Scale class
            if max_dim < _SCALE_THRESHOLDS[0]:
                scale = "micro"
            elif max_dim < _SCALE_THRESHOLDS[1]:
                scale = "small"
            elif max_dim < _SCALE_THRESHOLDS[2]:
                scale = "medium"
            elif max_dim < _SCALE_THRESHOLDS[3]:
                scale = "large"
            else:
                scale = "hero"
            return {"bbox_volume": float(vol), "bbox_height": float(height), "scale_class": scale}
        # Try bpy path: use bound_box.foreach_get if available
        bbox_volume = 0.0
        bbox_height = 0.0
        max_dim = 0.0
        try:
            bound_box = getattr(obj, "bound_box", None)
            if bound_box is not None:
                # Try foreach_get for performance
                try:
                    # bound_box is iterable of 8 vectors; try NumPy-style foreach_get on mesh?
                    if hasattr(bound_box, "foreach_get"):
                        import bpy as _bpy  # noqa: F401
                        # Not used directly; fallback to manual
                        pass
                except Exception:
                    pass
                # Manual computation from bound_box corners
                try:
                    corners = [list(corner) for corner in bound_box]
                    # bound_box stores 8 corners in local space
                    xs = [c[0] for c in corners]
                    ys = [c[1] for c in corners]
                    zs = [c[2] for c in corners]
                    dx = max(xs) - min(xs) if xs else 0.0
                    dy = max(ys) - min(ys) if ys else 0.0
                    dz = max(zs) - min(zs) if zs else 0.0
                    # Apply object scale if available
                    try:
                        scale = getattr(obj, "scale", None)
                        if scale is not None:
                            dx *= float(scale[0])
                            dy *= float(scale[1])
                            dz *= float(scale[2])
                    except Exception:
                        pass
                    bbox_volume = float(dx * dy * dz)
                    bbox_height = float(dz)
                    max_dim = max(dx, dy, dz)
                except Exception:
                    pass
            # Fallback to dimensions
            if max_dim == 0.0:
                dims = getattr(obj, "dimensions", None)
                if dims is not None:
                    try:
                        dx, dy, dz = float(dims[0]), float(dims[1]), float(dims[2])
                        bbox_volume = float(dx * dy * dz)
                        bbox_height = float(dz)
                        max_dim = max(dx, dy, dz)
                    except Exception:
                        pass
        except Exception:
            pass
        if max_dim < _SCALE_THRESHOLDS[0]:
            scale = "micro"
        elif max_dim < _SCALE_THRESHOLDS[1]:
            scale = "small"
        elif max_dim < _SCALE_THRESHOLDS[2]:
            scale = "medium"
        elif max_dim < _SCALE_THRESHOLDS[3]:
            scale = "large"
        else:
            scale = "hero"
        return {"bbox_volume": float(bbox_volume), "bbox_height": float(bbox_height), "scale_class": scale}
    except Exception:
        return {"bbox_volume": 0.0, "bbox_height": 0.0, "scale_class": "medium"}


def extract_material_nodes(obj: Any) -> dict[str, Any]:
    """Inspect material node trees for Principled BSDF properties."""
    try:
        if isinstance(obj, dict):
            # Offline fallback
            has_sub = bool(obj.get("has_subsurface", False))
            has_met = bool(obj.get("has_metallic", False))
            has_emit = bool(obj.get("has_emission", False))
            shader = str(obj.get("shader_type", "unknown"))
            if shader == "unknown":
                if has_sub:
                    shader = "skin"
                elif has_emit:
                    shader = "emissive"
                elif has_met:
                    shader = "metal"
                elif obj.get("material_names"):
                    shader = "standard"
            return {
                "has_subsurface": bool(has_sub),
                "has_metallic": bool(has_met),
                "has_emission": bool(has_emit),
                "shader_type": shader,
            }
        has_subsurface = False
        has_metallic = False
        has_emission = False
        shader_type = "unknown"
        found_principled = False
        try:
            # Collect materials from slots
            mats: list[Any] = []
            slots = getattr(obj, "material_slots", None)
            if slots is not None:
                for slot in slots:
                    mat = getattr(slot, "material", None)
                    if mat is not None:
                        mats.append(mat)
            else:
                # obj itself may be a material datablock
                if getattr(getattr(obj, "bl_rna", None), "identifier", "") == "MATERIAL":
                    mats.append(obj)
                else:
                    mat = getattr(obj, "active_material", None)
                    if mat is not None:
                        mats.append(mat)
            for mat in mats:
                node_tree = getattr(mat, "node_tree", None)
                if node_tree is None:
                    continue
                nodes = getattr(node_tree, "nodes", None)
                if nodes is None:
                    continue
                for node in nodes:
                    try:
                        bl_id = str(getattr(node, "bl_idname", ""))
                        ntype = str(getattr(node, "type", ""))
                        is_principled = bl_id == "ShaderNodeBsdfPrincipled" or ntype == "BSDF_PRINCIPLED"
                        if not is_principled:
                            continue
                        found_principled = True
                        inputs = getattr(node, "inputs", None)
                        if inputs is None:
                            continue
                        # Helper to get input value
                        def _input_value(name: str) -> float | None:
                            try:
                                for inp in inputs:
                                    if str(getattr(inp, "name", "")).casefold() == name.casefold() or str(getattr(inp, "identifier", "")).casefold() == name.casefold():
                                        val = getattr(inp, "default_value", None)
                                        if isinstance(val, (int, float)):
                                            return float(val)
                                        # For vector/color, check emission strength via separate input
                                        if hasattr(val, "__len__"):
                                            try:
                                                return float(val[0])
                                            except Exception:
                                                return None
                                return None
                            except Exception:
                                return None

                        # Subsurface weight
                        sub = _input_value("Subsurface Weight")
                        if sub is None:
                            sub = _input_value("Subsurface")
                        if sub is not None and sub > 0.001:
                            has_subsurface = True
                        # Metallic
                        met = _input_value("Metallic")
                        if met is not None and met > 0.3:
                            has_metallic = True
                        # Emission strength
                        emit = _input_value("Emission Strength")
                        if emit is None:
                            emit = _input_value("Emission")
                        if emit is not None and emit > 0.001:
                            has_emission = True
                        # Emission Color may indicate emission if strength missing but color non-black
                        if not has_emission:
                            try:
                                for inp in inputs:
                                    if str(getattr(inp, "name", "")).casefold() in ("emission color", "emission_colour"):
                                        val = getattr(inp, "default_value", None)
                                        if val is not None and hasattr(val, "__len__") and len(val) >= 3:
                                            if any(float(v) > 0.01 for v in val[:3]):
                                                # If emission strength not present, treat colored emission as emissive
                                                has_emission = True
                            except Exception:
                                pass
                    except Exception:
                        continue
        except Exception:
            pass
        if has_subsurface:
            shader_type = "skin"
        elif has_emission:
            shader_type = "emissive"
        elif has_metallic:
            shader_type = "metal"
        elif found_principled:
            shader_type = "standard"
        else:
            shader_type = "unknown"
        return {
            "has_subsurface": bool(has_subsurface),
            "has_metallic": bool(has_metallic),
            "has_emission": bool(has_emission),
            "shader_type": shader_type,
        }
    except Exception:
        return {
            "has_subsurface": False,
            "has_metallic": False,
            "has_emission": False,
            "shader_type": "unknown",
        }


def extract_deep_facts(obj: Any, base_facts: dict[str, Any]) -> dict[str, Any]:
    """Merge deep rig/animation/material facts into base_facts.

    Gracefully handles obj is None or bpy not available: returns base_facts
    unchanged (plus offline inference from existing keys when possible).
    Uses try/except around every bpy API access.
    """
    if base_facts is None:
        base_facts = {}
    # Ensure we return a dict (copy to avoid mutating caller unexpectedly? Merge in place per spec)
    result = dict(base_facts)
    # If obj is None, try offline inference from base_facts
    if obj is None:
        try:
            # Infer from existing keys if present; otherwise keep defaults via individual extractors using facts dict
            topo = extract_armature_topology(result)
            # Only merge if we got meaningful bone_count >0 or has_facial etc to avoid overwriting?
            # Always merge but do not overwrite existing explicit keys? Use setdefault semantics via merging only if not already present
            for k, v in topo.items():
                if k not in result:
                    result[k] = v
                else:
                    # If result already has a value, keep it but ensure topology still inferred for has_tail etc
                    # If bone_count was 0 and we inferred >0, update?
                    if k == "rig_topology" and result.get(k) == "unknown" and v != "unknown":
                        result[k] = v
            shape = extract_shape_keys(result)
            for k, v in shape.items():
                if k not in result:
                    result[k] = v
            anim = extract_animation_data(result)
            for k, v in anim.items():
                if k not in result:
                    result[k] = v
            vol = extract_bounding_volume(result)
            for k, v in vol.items():
                if k not in result:
                    result[k] = v
            mat = extract_material_nodes(result)
            for k, v in mat.items():
                if k not in result:
                    result[k] = v
        except Exception:
            pass
        return result
    # obj is not None: try each extractor with bpy
    for extractor in (
        extract_armature_topology,
        extract_shape_keys,
        extract_animation_data,
        extract_bounding_volume,
        extract_material_nodes,
    ):
        try:
            facts = extractor(obj)
            if isinstance(facts, dict):
                result.update(facts)
        except Exception:
            continue
    # Also fallback to base_facts inference for missing keys (offline keys like bone_names may supplement)
    try:
        # If bone_names present in base_facts but extractor didn't find tail due to bpy mismatch, supplement
        if "bone_names" in base_facts and result.get("bone_count", 0) == 0:
            extra = extract_armature_topology(base_facts)
            for k, v in extra.items():
                if result.get(k) in (0, False, "unknown", None) and v not in (0, False, "unknown", None, []):
                    result[k] = v
                elif k not in result:
                    result[k] = v
    except Exception:
        pass
    return result
