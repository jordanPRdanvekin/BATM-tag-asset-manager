"""Single source of truth for AutoTag rule match fields.

This module is the only place that defines which fields a Rule can match.
The UI (operators/rules.py), the validator (core/rules.py) and the tests all
consume this contract, so they can never diverge.
"""

from __future__ import annotations

# (identifier, label, description) — consumed by EnumProperty in the UI.
RULE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("name_tokens", "Name Tokens", "Tokens extracted from the asset name"),
    ("id_type", "ID Type", "Blender asset ID type"),
    ("object_type", "Object Type", "Object type such as MESH or LIGHT"),
    ("data_type", "Data Type", "Underlying datablock type"),
    ("collection_types", "Collection Types", "Object types contained in a Collection"),
    ("library_reference", "Library", "Owning Asset Library name"),
    ("path_tokens", "Path Tokens", "Tokens extracted from the .blend path"),
    ("library_tag", "Library Tag", "Source library marked with a knowledge Tag"),
    ("object_names", "Object Names", "Names of first-level Object children"),
    ("object_types", "Object Types", "First-level Object child types"),
    ("modifier_types", "Modifier Types", "Modifiers found on the asset data"),
    ("material_names", "Material Names", "Materials assigned to the asset data"),
    ("rig_types", "Rig Types", "Armature / rig structure of the asset"),
    ("geometry_node_names", "Geometry Node Names", "Node groups used by the asset"),
    ("bone_names", "Bone Names", "Bone names found in armatures"),
    ("constraint_types", "Constraint Types", "Constraint types found on the asset"),
    ("parent_names", "Parent Names", "Parent / family object names"),
    ("hierarchy_depth", "Hierarchy Depth", "Maximum depth of the object hierarchy"),
)

# Set of valid identifiers — consumed by validation and tests.
ALLOWED_FIELDS: frozenset[str] = frozenset(identifier for identifier, _label, _desc in RULE_FIELDS)