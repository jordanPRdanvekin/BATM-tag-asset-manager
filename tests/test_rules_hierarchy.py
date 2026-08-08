"""Hierarchy facts are usable rule fields and safe to match (D1)."""

from _setup import ADDON_ROOT  # noqa: F401

from batm.core.models import AssetKey, AssetSnapshot, Rule
from batm.core.rules import ALLOWED_FIELDS, validate_rule
from batm.engine.autotag import build_autotag_operations, rule_matches

KEY = AssetKey(
    library_reference="Lib",
    blend_path="C:/lib/rig.blend",
    id_type="OBJECT",
    datablock_name="RobotArm",
)


def snapshot_with(facts):
    return AssetSnapshot(key=KEY, tags=[], writable=True, fingerprint={}, facts=dict(facts))


def test_hierarchy_fields_are_allowed():
    for field in ("bone_names", "constraint_types", "parent_names", "hierarchy_depth"):
        assert field in ALLOWED_FIELDS, field


def test_rules_validate_with_hierarchy_fields():
    rule = Rule(
        name="Rigged prop",
        match_field="constraint_types",
        match_values=["TRACK_TO"],
        add_tags=["Rigged"],
    )
    assert validate_rule(rule) == []


def test_rule_matches_hierarchy_facts():
    snap = snapshot_with({"constraint_types": ["TRACK_TO", "LIMIT_LOCATION"]})
    rule = Rule(name="T", match_field="constraint_types", match_values=["track_to"], add_tags=["Rigged"])
    assert rule_matches(snap, rule)
    assert rule_matches(snap, Rule(name="A", match_field="bone_names", match_values=["root"], add_tags=["X"])) is False


def test_autotag_applies_hierarchy_rules():
    snap = snapshot_with(
        {
            "bone_names": ["root", "upper_arm"],
            "constraint_types": ["TRACK_TO"],
            "parent_names": ["Robot"],
            "hierarchy_depth": 4,
        }
    )
    rules = [
        Rule(name="R", match_field="bone_names", match_values=["upper_arm"], add_tags=["Armed"]),
        Rule(name="H", match_field="hierarchy_depth", match_values=["4"], add_tags=["Deep"]),
    ]
    ops = build_autotag_operations([snap], rules)
    values = [value for op in ops for value in op.values]
    assert "Armed" in values, values
    assert "Deep" in values, values


print("RULES HIERARCHY TESTS OK")