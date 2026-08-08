"""Full declarative AutoTag rule editor."""

from __future__ import annotations

import copy

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..adapters.rule_store import reset_rules, save_active_rules
from ..adapters.storage import atomic_json_write
from ..core.models import Rule
from ..core.rules import load_rules, rules_payload, validate_rule
from ..core.sanitizer import split_tag_input
from ..core.session import SESSION


FIELDS = [
    ("name_tokens", "Name Tokens", "Tokens extracted from the asset name"),
    ("id_type", "ID Type", "Blender asset ID type"),
    ("object_type", "Object Type", "Object type such as MESH or LIGHT"),
    ("data_type", "Data Type", "Underlying datablock type"),
    ("collection_types", "Collection Types", "Object types contained in a Collection"),
    ("library_reference", "Library", "Owning Asset Library name"),
    ("path_tokens", "Path Tokens", "Tokens extracted from the .blend path"),
]


def _save(context) -> None:
    SESSION.rules.sort(key=lambda rule: rule.priority)
    save_active_rules(SESSION.rules)
    context.window_manager.batm_runtime.status = "AutoTag rules saved"


class BATM_OT_rule_edit(bpy.types.Operator):
    bl_idname = "batm.rule_edit"
    bl_label = "AutoTag Rule"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)
    rule_name: StringProperty(name="Name")
    enabled: BoolProperty(name="Enabled", default=True)
    priority: IntProperty(name="Priority", default=100, min=0, max=10000)
    match_field: EnumProperty(name="Match Field", items=FIELDS, default="name_tokens")
    match_mode: EnumProperty(
        name="Match Mode",
        items=[("ANY", "Any", "Any expected value"), ("ALL", "All", "All expected values"), ("EXACT", "Exact", "Exact set match")],
        default="ANY",
    )
    match_values: StringProperty(name="Match Values", description="Comma-separated values")
    add_tags: StringProperty(name="Add Tags", description="Comma-separated Tags")

    def invoke(self, context, _event):
        if 0 <= self.index < len(SESSION.rules):
            rule = SESSION.rules[self.index]
            self.rule_name = rule.name
            self.enabled = rule.enabled
            self.priority = rule.priority
            self.match_field = rule.match_field
            self.match_mode = rule.match_mode
            self.match_values = ", ".join(rule.match_values)
            self.add_tags = ", ".join(rule.add_tags)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "rule_name")
        row = layout.row(align=True)
        row.prop(self, "enabled")
        row.prop(self, "priority")
        layout.prop(self, "match_field")
        layout.prop(self, "match_mode")
        layout.prop(self, "match_values")
        layout.prop(self, "add_tags")

    def execute(self, context):
        rule = Rule(
            name=self.rule_name,
            enabled=self.enabled,
            priority=self.priority,
            match_field=self.match_field,
            match_mode=self.match_mode,
            match_values=[value.strip() for value in split_tag_input(self.match_values) if value.strip()],
            add_tags=[value.strip() for value in split_tag_input(self.add_tags) if value.strip()],
        )
        errors = validate_rule(rule)
        if errors:
            self.report({"ERROR"}, "; ".join(errors))
            return {"CANCELLED"}
        if 0 <= self.index < len(SESSION.rules):
            rule.rule_id = SESSION.rules[self.index].rule_id
            SESSION.rules[self.index] = rule
        else:
            SESSION.rules.append(rule)
        _save(context)
        return {"FINISHED"}


class BATM_OT_rule_toggle(bpy.types.Operator):
    bl_idname = "batm.rule_toggle"
    bl_label = "Enable / Disable Rule"
    index: IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(SESSION.rules):
            SESSION.rules[self.index].enabled = not SESSION.rules[self.index].enabled
            _save(context)
        return {"FINISHED"}


class BATM_OT_rule_duplicate(bpy.types.Operator):
    bl_idname = "batm.rule_duplicate"
    bl_label = "Duplicate Rule"
    index: IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(SESSION.rules):
            rule = copy.deepcopy(SESSION.rules[self.index])
            rule.rule_id = Rule(name="temporary").rule_id
            rule.name += " Copy"
            rule.priority += 1
            SESSION.rules.append(rule)
            _save(context)
        return {"FINISHED"}


class BATM_OT_rule_delete(bpy.types.Operator):
    bl_idname = "batm.rule_delete"
    bl_label = "Delete Rule"
    index: IntProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        if 0 <= self.index < len(SESSION.rules):
            del SESSION.rules[self.index]
            _save(context)
        return {"FINISHED"}


class BATM_OT_rules_reset(bpy.types.Operator):
    bl_idname = "batm.rules_reset"
    bl_label = "Restore Bundled Rules"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        SESSION.rules = reset_rules()
        context.window_manager.batm_runtime.status = "Bundled rules restored"
        return {"FINISHED"}


class BATM_OT_rules_import(bpy.types.Operator, ImportHelper):
    bl_idname = "batm.rules_import"
    bl_label = "Import AutoTag Rules"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        try:
            SESSION.rules = load_rules(self.filepath)
            _save(context)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BATM_OT_rules_export(bpy.types.Operator, ExportHelper):
    bl_idname = "batm.rules_export"
    bl_label = "Export AutoTag Rules"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, _context):
        atomic_json_write(self.filepath, rules_payload(SESSION.rules))
        return {"FINISHED"}


CLASSES = (
    BATM_OT_rule_edit,
    BATM_OT_rule_toggle,
    BATM_OT_rule_duplicate,
    BATM_OT_rule_delete,
    BATM_OT_rules_reset,
    BATM_OT_rules_import,
    BATM_OT_rules_export,
)
