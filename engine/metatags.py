"""MetaTags: objective structural facts derived directly from the asset.

These are independent of the concept catalog. They come from Blender metadata
(id_type, object types present, contained object structure) and are always
explainable as ``Metadata -> <fact>``. No AI, no catalogs — pure deterministic
facts.
"""

from __future__ import annotations

from ..core.models import AssetSnapshot, TagOperation

# Object types that are meaningful, stable English tags.
_CONTAINED_TYPES = {"CAMERA", "LIGHT", "EMPTY", "ARMATURE", "CURVE", "GREASE_PENCIL", "LATTICE", "SPEAKER", "VOLUME", "MESH"}
# The asset's own data type already tagged by base rules should not become
# a noisy "ContainsMesh" on top of "Mesh".
_PRIMARY_CONTENT = {"OBJECT", "COLLECTION", "MATERIAL", "WORLD", "ACTION", "NODETREE"}


def _id_tag(id_type: str) -> str | None:
    mapping = {
        "OBJECT": "Object",
        "COLLECTION": "Collection",
        "MATERIAL": "Material",
        "WORLD": "World",
        "ACTION": "Action",
        "NODETREE": "NodeGroup",
        "GREASE_PENCIL": "GreasePencil",
    }
    normalized = str(id_type).upper()
    return mapping.get(normalized, None)


def metatag_operations(snapshot: AssetSnapshot) -> list[TagOperation]:
    """Propose objective fact tags for one asset (already enriched)."""
    ops: list[TagOperation] = []
    target = snapshot.key.token
    facts = snapshot.facts
    id_type = str(facts.get("id_type") or snapshot.key.id_type).upper()

    id_tag = _id_tag(id_type)
    if id_tag:
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[id_tag],
                origin="AUTO",
                priority=5,
                explanation=f"Metadata -> id_type={id_type}",
            )
        )

    object_types = sorted({str(t).upper() for t in (facts.get("object_types") or []) if t})
    if not object_types:
        return ops

    primary = id_type
    for obj_type in object_types:
        if obj_type not in _CONTAINED_TYPES:
            continue
        if obj_type == "MESH":
            label = "Mesh"
        else:
            label = f"Contains{obj_type.title()}"
        # Avoid redundant Contains* when the type is the asset's own identity.
        if obj_type in _PRIMARY_CONTENT and obj_type == primary and obj_type != "MESH":
            continue
        ops.append(
            TagOperation(
                kind="ADD",
                targets=[target],
                values=[label],
                origin="AUTO",
                priority=25,
                explanation=f"Metadata -> object.type={obj_type}",
            )
        )
    return ops
