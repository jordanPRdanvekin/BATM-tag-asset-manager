"""Extractor contract tests (no Blender required)."""

import sys
from types import SimpleNamespace

from _setup import ADDON_ROOT  # noqa: F401

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


mesh_obj = asObj("TreeMesh", "MESH", data=idents("MESH"))
light_obj = asObj("SunLight", "LIGHT", data=idents("LIGHT"))

col = FakeCollection("Forest", [mesh_obj, light_obj])
mat = FakeMaterial("Glass BSDF")

facts = extract_all(col)
assert facts["id_type"] == "COLLECTION"
assert facts["object_type"] == ""
assert facts["data_type"] == ""
assert facts["collection_types"] == ["LIGHT", "MESH"]
assert facts["object_types"] == ["LIGHT", "MESH"]
assert facts["collection_object_count"] == 2

facts = extract_all(mesh_obj)
assert facts["id_type"] == "OBJECT"
assert facts["object_type"] == "MESH"
assert facts["data_type"] == "MESH"
assert facts["collection_types"] == ["MESH"]

facts = extract_all(mat)
assert facts["id_type"] == "MATERIAL"
assert "Glass BSDF" in facts["material_names"]
assert facts["object_type"] == ""
assert facts["object_types"] == []

print("EXTRACTOR CONTRACT OK")