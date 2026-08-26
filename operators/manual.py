"""Queue-only Manual Tag Editor operators."""

from __future__ import annotations

import math

import bpy
from bpy.props import StringProperty

from ..adapters.blender_assets import selected_asset_keys, selected_assets, selected_tag_frequency
from ..core.models import TagOperation
from ..core.sanitizer import options_from_props, split_tag_input
from ..core.session import SESSION, filter_tag_list


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
    bl_description = "Queue the entered Tags to be added to every selected asset (reviewed before applying)"
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
    bl_description = "Toggle the selection of this existing Tag for multi-select operations"

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
    bl_description = "Queue the removal of every selected Tag across the selected assets (reviewed before applying)"
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
    bl_description = "Replace every selected Tag with the destination across the selected assets (reviewed before applying)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _runtime(context)
        targets = _targets(context)
        sources = sorted(SESSION.selected_tags)
        values = [value.strip() for value in split_tag_input(props.replace_destination) if value.strip()]
        if not targets or not sources or not values:
            self.report({"WARNING"}, "Select assets, select the Tags to replace and enter the destination")
            return {"CANCELLED"}
        for source in sources:
            SESSION.operations.append(TagOperation(kind="REPLACE", targets=targets, source_value=source, values=list(values), origin="MANUAL", explanation=f"Manual Replace / Merge: {source}"))
        props.replace_destination = ""
        SESSION.selected_tags.clear()
        props.status = f"Queued Replace for {len(sources)} selected Tag(s) across {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_clear_pending(bpy.types.Operator):
    bl_idname = "batm.clear_pending"
    bl_label = "Clear Pending Operations"
    bl_description = "Remove every pending operation (Add, Remove and Replace) from the queue before it is applied"

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


class BATM_OT_pending_cancel(bpy.types.Operator):
    """Remove one pending operation from the queue (nothing is written to assets)."""

    bl_idname = "batm.pending_cancel"
    bl_label = "Cancel Pending Operation"
    bl_description = "Remove this single pending operation from the queue before it is applied"
    operation_id: StringProperty()

    def execute(self, context):
        for operation in list(SESSION.operations):
            if operation.operation_id != self.operation_id:
                continue
            SESSION.operations.remove(operation)
            _runtime(context).status = (
                f"Cancelled pending '{operation.kind}' {operation.values} for {len(operation.targets)} assets"
            )
            _recompile_review(context)
            return {"FINISHED"}
        return {"CANCELLED"}


class BATM_OT_pending_clear_kind(bpy.types.Operator):
    """Clear all pending operations of one kind (Add, Remove or Replace), keeping the rest."""

    bl_idname = "batm.pending_clear_kind"
    bl_label = "Clear Pending Kind"
    bl_description = "Remove every pending operation of the chosen kind (Add, Remove or Replace) while keeping the other queues"
    kind: StringProperty()

    def execute(self, context):
        kind = self.kind.upper()
        if kind not in {"ADD", "REMOVE", "REPLACE"}:
            return {"CANCELLED"}
        before = len(SESSION.operations)
        SESSION.operations[:] = [op for op in SESSION.operations if op.kind != kind]
        removed = before - len(SESSION.operations)
        _runtime(context).status = f"Cleared {removed} pending {kind} operation(s)"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_manual_clone_selected(bpy.types.Operator):
    """Clone all tags from one source asset to every other selected asset."""

    bl_idname = "batm.manual_clone_selected"
    bl_label = "Clone Selected"
    bl_description = "Queue copying every Tag of one source asset to the other selected assets (reviewed before applying)"
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
        # Zip keeps the live keys and AssetRepresentations in the same order so
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
        if not targets:
            self.report({"WARNING"}, "Select at least one other asset to clone Tags onto")
            return {"CANCELLED"}
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
    """Move through the Manual Tag Editor list: first / previous / next / last page."""

    bl_idname = "batm.manual_tag_page"
    bl_label = "Tag Page"
    bl_description = "Browse the Tag list page by page (first / previous / next / last)"
    action: StringProperty()

    def execute(self, context):
        props = _runtime(context)
        page_size = max(1, int(props.manual_page_size))
        _asset_total, frequency = selected_tag_frequency(context)
        visible = filter_tag_list(frequency, search=props.tag_search, length=props.tag_length_filter)
        page_count = max(1, math.ceil(max(1, len(visible)) / page_size))
        current = props.manual_tag_page
        if self.action == "FIRST":
            props.manual_tag_page = 0
        elif self.action == "LAST":
            props.manual_tag_page = page_count - 1
        elif self.action == "PREV":
            props.manual_tag_page = max(0, current - 1)
        elif self.action == "NEXT":
            props.manual_tag_page = min(page_count - 1, current + 1)
        return {"FINISHED"}


class BATM_OT_manual_select_all(bpy.types.Operator):
    """Select every Tag currently visible in the Manual Tag Editor."""

    bl_idname = "batm.manual_select_all"
    bl_label = "Select All Tags"
    bl_description = "Select every Tag currently listed in the Manual Tag Editor (respects the search filter)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _runtime(context)
        _asset_total, frequency = selected_tag_frequency(context)
        visible = filter_tag_list(frequency, search=props.tag_search, length=props.tag_length_filter)
        for name, _count in visible:
            SESSION.selected_tags.add(name.casefold())
        props.status = f"Selected {len(SESSION.selected_tags)} Tags"
        return {"FINISHED"}


class BATM_OT_manual_clear_selection(bpy.types.Operator):
    """Clear the current Tag selection in the Manual Tag Editor."""

    bl_idname = "batm.manual_clear_selection"
    bl_label = "Clear Tag Selection"
    bl_description = "Deselect every Tag currently selected in the Manual Tag Editor"
    bl_options = {"REGISTER"}

    def execute(self, _context):
        SESSION.selected_tags.clear()
        return {"FINISHED"}


CLASSES = (
    BATM_OT_manual_add,
    BATM_OT_toggle_tag_selection,
    BATM_OT_manual_remove_one,
    BATM_OT_manual_remove,
    BATM_OT_manual_replace,
    BATM_OT_clear_pending,
    BATM_OT_pending_cancel,
    BATM_OT_pending_clear_kind,
    BATM_OT_manual_clone_selected,
    BATM_OT_manual_tag_page,
    BATM_OT_manual_select_all,
    BATM_OT_manual_clear_selection,
)
