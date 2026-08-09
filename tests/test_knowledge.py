"""Knowledge base tests (no Blender required)."""

from _setup import ADDON_ROOT  # noqa: F401

from batm.core.models import AssetKey, AssetSnapshot
from batm.engine.autotag import build_autotag_operations
from batm.engine.knowledge import knowledge_tags, load_knowledge, word_index

KEY = AssetKey(
    library_reference="Test Library",
    blend_path="C:/lib/meshes.blend",
    id_type="OBJECT",
    datablock_name="OakTable",
)
SNAPSHOT = AssetSnapshot(key=KEY, tags=[], writable=True, fingerprint={}, facts={})


def snapshot_with(facts):
    return AssetSnapshot(key=KEY, tags=[], writable=True, fingerprint={}, facts=dict(facts))


def test_bundled_resource_loads():
    groups = load_knowledge()
    assert groups, "bundled knowledge must load with groups"
    assert all(g["tag"] and g["words"] for g in groups)
    index = word_index(groups)
    assert index.get("oak") == "Wood"
    print(f"Bundled knowledge OK ({len(groups)} groups)")


def test_knowledge_tags_match_facts():
    snap = snapshot_with({"name_tokens": ["oak"]})
    tags = knowledge_tags(snap, load_knowledge())
    assert ("Wood", "oak") in tags, f"expected Wood tag, got {tags}"
    snap2 = snapshot_with({"material_names": ["Plywood", "Glass"]})
    tags2 = knowledge_tags(snap2, load_knowledge())
    assert ("Wood", "plywood") in tags2, f"expected Wood/plywood, got {tags2}"


def test_no_false_positives():
    snap = snapshot_with({"name_tokens": ["unrelated", "potato"]})
    assert knowledge_tags(snap, load_knowledge()) == []


def test_autotag_includes_knowledge():
    groups = [{"tag": "Stylized", "words": ["cartoon", "game"]}]
    snap = snapshot_with({"name_tokens": ["game", "prop"]})
    ops = build_autotag_operations([snap], [], groups)
    values = [value for op in ops for value in op.values]
    assert "Stylized" in values
    assert all(op.origin == "AUTO" for op in ops)


def test_autotag_without_knowledge_unchanged():
    snap = snapshot_with({"name_tokens": ["game", "prop"]})
    ops = build_autotag_operations([snap], [], None)
    values = [value for op in ops for value in op.values]
    assert "Stylized" not in values


if __name__ == "__main__":
    test_bundled_resource_loads()
    test_knowledge_tags_match_facts()
    test_no_false_positives()
    test_autotag_includes_knowledge()
    test_autotag_without_knowledge_unchanged()
    print("KNOWLEDGE TESTS OK")