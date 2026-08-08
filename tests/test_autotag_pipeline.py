"""AutoTag pipeline tests over the unified extractor contract (no Blender)."""

import json
import sys
from types import SimpleNamespace

from _setup import ADDON_ROOT  # noqa: F401

from batm.core.models import AssetKey, AssetSnapshot
from batm.core.reducer import compile_states
from batm.core.rules import load_rule_payload
from batm.engine.autotag import build_autotag_operations
from batm.engine.extractors import extract_all


class RNA:
    identifier = ""


class FakeObj:
    def __init__(self, name, obj_type, data=None):
        self.name = name
        self.type = obj_type
        self.data = data
        self.bl_rna = RNA()
        self.bl_rna.identifier = "OBJECT"


class FakeCollection:
    def __init__(self, name, objects):
        self.name = name
        self.all_objects = objects
        self.bl_rna = SimpleNamespace(identifier="COLLECTION")


class FakeMaterial:
    def __init__(self, name):
        self.name = name
        self.bl_rna = SimpleNamespace(identifier="MATERIAL")


def asObj(name, obj_type, data=None):
    return FakeObj(name, obj_type, data)


def idents(blob):
    return SimpleNamespace(bl_rna=SimpleNamespace(identifier=blob))


with open(ADDON_ROOT + r"\resources\autotag_rules.json", encoding="utf-8") as handle:
    rules = load_rule_payload(json.load(handle))


def snapshot_for(datablock, key_name, lib="Test Lib", id_type=None):
    if id_type is None:
        id_type = datablock.bl_rna.identifier
    key = AssetKey(
        library_reference=lib,
        blend_path=r"C:\fake\file.blend",
        id_type=id_type,
        datablock_name=key_name,
    )
    return AssetSnapshot(key=key, facts=extract_all(datablock), writable=True)


mesh = asObj("OakTree", "MESH", data=idents("MESH"))
col = FakeCollection("Forest", [mesh, asObj("Rock", "MESH", data=idents("MESH"))])
mat = FakeMaterial("Gold Metal")
light = asObj("StudioLight", "LIGHT", data=idents("LIGHT"))

snapshots = [
    snapshot_for(mesh, "OakTree", lib="Comedy Island"),
    snapshot_for(col, "Forest"),
    snapshot_for(mat, "GoldMetal"),
    snapshot_for(light, "StudioLight"),
]

ops = build_autotag_operations(snapshots, rules)

tags_for = {}
for op in ops:
    for target in op.targets:
        name = target.split("|")[-1]
        tags_for.setdefault(name, []).append(op.values[0])

assert "Mesh" in tags_for["OakTree"], tags_for.get("OakTree")
assert "Nature" in tags_for["OakTree"], tags_for.get("OakTree")
assert "Prop" in tags_for["OakTree"], tags_for.get("OakTree")
assert "Collection" in tags_for["Forest"], tags_for.get("Forest")
assert "Material" in tags_for["GoldMetal"], tags_for.get("GoldMetal")
assert "Lighting" in tags_for["StudioLight"], tags_for.get("StudioLight")
assert "Comedy Island" in tags_for["OakTree"], tags_for.get("OakTree")

# Reducer keeps per-tag reasons after sanitization (D6).
states = compile_states(snapshots, ops)
for state in states.values():
    for tag in state.after:
        reasons = state.tag_reasons.get(tag.casefold(), [])
        assert reasons, f"tag {tag} of {state.key.datablock_name} has no recorded reason"
    assert state.tag_reasons, f"{state.key.datablock_name} has no reasons at all"

# Manual ADD reason is reported per Tag too.
from batm.core.models import TagOperation  # noqa: E402

manual = TagOperation(
    kind="ADD",
    targets=[states[snapshots[0].key.token].key.token],
    values=["Custom Tag"],
    origin="MANUAL",
    explanation="Manual Add",
)
states = compile_states(snapshots, ops + [manual])
oak = states[snapshots[0].key.token]
assert "custom tag" in oak.tag_reasons and "Manual Add" in oak.tag_reasons["custom tag"], oak.tag_reasons

print("AUTOTAG PIPELINE OK (contract + reasons)")