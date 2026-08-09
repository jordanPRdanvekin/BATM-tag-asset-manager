"""Queue-only Manual Tag Editor operators."""

from __future__ import annotations

import math

import bpy
from bpy.props import IntProperty, StringProperty

from ..adapters.blender_assets import selected_asset_keys, selected_assets, selected_tag_frequency
from ..core.models import TagOperation
from ..core.sanitizer import options_from_props, split_tag_input
from ..core.session import SESSION


def _targets(context) -> list[str]:
    return [key.token for key in selected_asset_keys(context)]


def _recompile_review(context) -> None:
    """Recompile with the current sanitize settings, preserving disabled states."""
    SESSION.sanitize_options = options_from_props(context.window_manager.batm_runtime)
    if SESSION.phase == "REVIEW_READY":
        SESSION.recompile_preserving_disabled()
        context.window_manager.batm_runtime.status = "Review updated"


def _runtime(context):
    return context.window_manager.batm_runtime


def _is_duplicate(operation: TagOperation) -> bool:
    """True when an identical pending operation (same kind, targets, values) is queued."""
    for existing in SESSION.operations:
        if existing.kind != operation.kind:
            continue
        if set(existing.targets) != set(operation.targets):
            continue
        if set(existing.values) != set(operation.values):
            continue
        if existing.source_value != operation.source_value:
            continue
        return True
    return False


class BATM_OT_manual_add(bpy.types.Operator):
    bl_idname = "batm.manual_add"
    bl_label = "Queue Add Tags"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _runtime(context)
        values = [value.strip() for value in split_tag_input(props.manual_add) if value.strip()]
        targets = _targets(context)
        if not values or not targets:
            self.report({"WARNING"}, "Select assets and enter one or more Tags")
            return {"CANCELLED"}
        operation = TagOperation(kind="ADD", targets=targets, values=values, origin="MANUAL", explanation="Manual Add")
        if _is_duplicate(operation):
            props.status = f"Already queued ADD for {len(targets)} assets"
            return {"FINISHED"}
        SESSION.operations.append(operation)
        props.manual_add = ""
        props.status = f"Queued Add for {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_toggle_tag_selection(bpy.types.Operator):
    bl_idname = "batm.toggle_tag_selection"
    bl_label = "Select Tag"

    tag_name: StringProperty()

    def execute(self, _context):
        key = self.tag_name.casefold()
        if key in SESSION.selected_tags:
            SESSION.selected_tags.remove(key)
        else:
            SESSION.selected_tags.add(key)
        return {"FINISHED"}


class BATM_OT_manual_remove_one(bpy.types.Operator):
    """Queue a REMOVE for a single existing Tag across the selection."""

    bl_idname = "batm.manual_remove_one"
    bl_label = "Queue Remove One Tag"
    bl_description = "Queue REMOVE of this Tag for every selected asset (1 click, no typing)"
    bl_options = {"REGISTER"}

    tag_name: StringProperty()

    def execute(self, context):
        targets = _targets(context)
        if not targets or not self.tag_name.strip():
            self.report({"WARNING"}, "Select assets with this Tag to remove it")
            return {"CANCELLED"}
        operation = TagOperation(
            kind="REMOVE",
            targets=targets,
            values=[self.tag_name],
            origin="MANUAL",
            explanation=f"Manual Remove: {self.tag_name}",
        )
        if _is_duplicate(operation):
            _runtime(context).status = "Already queued REMOVE for this Tag"
            return {"FINISHED"}
        SESSION.operations.append(operation)
        _runtime(context).status = f"Queued Remove '{self.tag_name}' for {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_manual_remove(bpy.types.Operator):
    bl_idname = "batm.manual_remove"
    bl_label = "Queue Remove Selected Tags"
    bl_options = {"REGISTER"}

    def execute(self, context):
        targets = _targets(context)
        values = sorted(SESSION.selected_tags)
        if not targets or not values:
            self.report({"WARNING"}, "Select assets and at least one existing Tag")
            return {"CANCELLED"}
        SESSION.operations.append(
            TagOperation(kind="REMOVE", targets=targets, values=values, origin="MANUAL", explanation="Manual Remove")
        )
        SESSION.selected_tags.clear()
        _runtime(context).status = f"Queued Remove for {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_manual_replace(bpy.types.Operator):
    bl_idname = "batm.manual_replace"
    bl_label = "Queue Replace / Merge"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _runtime(context)
        targets = _targets(context)
        source = props.replace_source.strip()
        values = [value.strip() for value in split_tag_input(props.replace_destination) if value.strip()]
        if not targets or not source or not values:
            self.report({"WARNING"}, "Select assets and provide source and destination Tags")
            return {"CANCELLED"}
        SESSION.operations.append(
            TagOperation(
                kind="REPLACE",
                targets=targets,
                source_value=source,
                values=values,
                origin="MANUAL",
                explanation="Manual Replace / Merge",
            )
        )
        props.replace_source = ""
        props.replace_destination = ""
        props.status = f"Queued Replace for {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_clear_pending(bpy.types.Operator):
    bl_idname = "batm.clear_pending"
    bl_label = "Clear Pending Operations"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if SESSION.phase not in {"IDLE", "REVIEW_READY"}:
            return {"CANCELLED"}
        SESSION.operations.clear()
        SESSION.selected_tags.clear()
        if SESSION.phase == "REVIEW_READY":
            SESSION.recompile_preserving_disabled()
        _runtime(context).status = "Pending operations cleared"
        return {"FINISHED"}


class BATM_OT_manual_clone_selected(bpy.types.Operator):
    """Clone all tags from one source asset to every other selected asset."""

    bl_idname = "batm.manual_clone_selected"
    bl_label = "Clone Selected"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _runtime(context)
        keys = selected_asset_keys(context)
        if len(keys) < 2:
            self.report({"WARNING"}, "Select at least two assets to clone tags")
            return {"CANCELLED"}
        source = props.clone_source.strip()
        source_token = source if source else keys[0].token
        if source_token not in {key.token for key in keys}:
            self.report({"WARNING"}, "Source asset is not in the current selection")
            return {"CANCELLED"}
        # Zip keep the live keys and AssetRepresentations in the same order so
        # the source token always resolves to the matching live asset.
        source_tags: list[str] = []
        for key, asset in zip(keys, selected_assets(context)):
            if key.token == source_token:
                source_tags = [tag.name for tag in asset.metadata.tags]
                break
        if not source_tags:
            desired_state = SESSION.desired.get(source_token)
            source_tags = list(desired_state.after) if desired_state else []
        if not source_tags:
            self.report({"WARNING"}, "Source asset has no tags to clone")
            return {"CANCELLED"}
        targets = [key.token for key in keys if key.token != source_token]
        SESSION.operations.append(
            TagOperation(
                kind="ADD",
                targets=targets,
                values=list(source_tags),
                origin="MANUAL",
                explanation=f"Clone from {source_token}",
            )
        )
        props.status = f"Queued clone of {len(source_tags)} tags to {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_manual_tag_page(bpy.types.Operator):
    """Change the pagination offset of the unified Tag list."""

    bl_idname = "batm.manual_tag_page"
    bl_label = "Tag Page"
    delta: IntProperty()

    def execute(self, context):
        props = _runtime(context)
        page_size = max(1, props.manual_page_size)
        _asset_total, frequency = selected_tag_frequency(context)
        page_count = max(1, math.ceil(max(1, len(frequency)) / page_size))
        current = props.manual_tag_page
        props.manual_tag_page = max(0, min(current + self.delta, page_count - 1))
        return {"FINISHED"}


CLASSES = (
    BATM_OT_manual_add,
    BATM_OT_toggle_tag_selection,
    BATM_OT_manual_remove_one,
    BATM_OT_manual_remove,
    BATM_OT_manual_replace,
    BATM_OT_clear_pending,
    BATM_OT_manual_clone_selected,
    BATM_OT_manual_tag_page,
)