"""AddonPreferences taxonomy operators: alias, import, export."""
from __future__ import annotations
import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from ..adapters.blender_assets import batm_preferences
from ..adapters.storage import atomic_json_write, read_json
from ..adapters.taxonomy_store import add_alias, load_user_overrides, save_user_overrides


class BATM_OT_taxonomy_add_alias(bpy.types.Operator):
    bl_idname = "batm.taxonomy_add_alias"
    bl_label = "Add Custom Tag Alias"
    bl_description = "Bind a user keyword to one or more catalog Tags so AutoTag can recognize it"
    bl_options = {"REGISTER"}
    term: StringProperty()
    tags: StringProperty()

    def execute(self, context):
        prefs = batm_preferences(context)
        term = (self.term or prefs.taxonomy_alias_term).strip()
        raw = self.tags or prefs.taxonomy_alias_tags
        values = [t.strip() for t in str(raw).split(",") if t.strip()]
        if not term or not values:
            self.report({"WARNING"}, "Provide an alias term and at least one tag")
            return {"CANCELLED"}
        add_alias(term, values)
        prefs.taxonomy_alias_term = ""
        prefs.taxonomy_alias_tags = ""
        prefs.taxonomy_search = term
        self.report({"INFO"}, f"Alias '{term}' -> {values}")
        return {"FINISHED"}


class BATM_OT_taxonomy_export(bpy.types.Operator, ExportHelper):
    bl_idname = "batm.taxonomy_export"
    bl_label = "Export Taxonomy Overrides"
    bl_description = "Write all user catalog overrides (aliases, categories, contextual terms) to a JSON file"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        try:
            atomic_json_write(self.filepath, load_user_overrides())
        except OSError as exc:
            self.report({"ERROR"}, f"Could not export the taxonomy overrides: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BATM_OT_taxonomy_import(bpy.types.Operator, ImportHelper):
    bl_idname = "batm.taxonomy_import"
    bl_label = "Import Taxonomy Overrides"
    bl_description = "Load user catalog overrides from a JSON file, replacing the current overrides"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        payload = read_json(self.filepath)
        if not isinstance(payload, dict):
            self.report({"ERROR"}, "Invalid taxonomy override file")
            return {"CANCELLED"}
        save_user_overrides(payload)
        return {"FINISHED"}


CLASSES = (BATM_OT_taxonomy_add_alias, BATM_OT_taxonomy_export, BATM_OT_taxonomy_import)
