"""Queue-only Manual Tag Editor operators."""

from __future__ import annotations

import math

import bpy
from bpy.props import IntProperty, StringProperty

from ..adapters.blender_assets import selected_asset_keys, selected_tag_frequency
from ..core.models import TagOperation
from ..core.session import SESSION
from ..core.sanitizer import split_tag_input


def _targets(context) -> list[str]:
    return [key.token for key in selected_asset_keys(context)]


def _recompile_review(context) -> None:
    if SESSION.phase == "REVIEW_READY":
        disabled = {token for token, state in SESSION.desired.items() if not state.enabled}
        SESSION.compile()
        for token in disabled:
            if token in SESSION.desired:
                SESSION.desired[token].enabled = False
        context.window_manager.batm_runtime.status = "Review updated"


class BATM_OT_manual_add(bpy.types.Operator):
    bl_idname = "batm.manual_add"
    bl_label = "Queue Add Tags"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.window_manager.batm_runtime
        values = [value.strip() for value in split_tag_input(props.manual_add) if value.strip()]
        targets = _targets(context)
        if not values or not targets:
            self.report({"WARNING"}, "Select assets and enter one or more Tags")
            return {"CANCELLED"}
        SESSION.operations.append(
            TagOperation(kind="ADD", targets=targets, values=values, origin="MANUAL", explanation="Manual Add")
        )
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
        context.window_manager.batm_runtime.status = f"Queued Remove for {len(targets)} assets"
        _recompile_review(context)
        return {"FINISHED"}


class BATM_OT_manual_replace(bpy.types.Operator):
    bl_idname = "batm.manual_replace"
    bl_label = "Queue Replace / Merge"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.window_manager.batm_runtime
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

    def execute(self, context):
        if SESSION.phase not in {"IDLE", "REVIEW_READY"}:
            return {"CANCELLED"}
        SESSION.operations.clear()
        SESSION.selected_tags.clear()
        if SESSION.phase == "REVIEW_READY":
            SESSION.compile()
        context.window_manager.batm_runtime.status = "Pending operations cleared"
        return {"FINISHED"}


class BATM_OT_manual_select_by_tag(bpy.types.Operator):
    """Select the asset(s) that own a given tag in the Asset Browser."""

    bl_idname = "batm.manual_select_by_tag"
    bl_label = "Select Assets with Tag"
    tag_name: StringProperty()

    def execute(self, context):
        from ..adapters.blender_assets import selected_assets

        try:
            assets = selected_assets(context)
            # Try the public selection API; some Blender builds expose
            # selected_assets.set() on the file browser params.
            params = getattr(getattr(context, "space_data", None), "params", None)
            if params is not None and hasattr(params, "selected_assets"):
                selection = getattr(params, "selected_assets", None)
                for asset in assets:
                    match = any(tag.name.casefold() == self.tag_name.casefold() for tag in asset.metadata.tags)
                    setattr(asset, "selected", match)
            context.window_manager.batm_runtime.status = f"Tag '{self.tag_name}' used to filter selection"
        except Exception:
            # Selection by tag is best-effort; the index still reflects tags.
            pass
        return {"FINISHED"}


class BATM_OT_manual_clone_selected(bpy.types.Operator):
    """Clone all tags from one source asset to every other selected asset."""

    bl_idname = "batm.manual_clone_selected"
    bl_label = "Clone Selected"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.window_manager.batm_runtime
        from ..adapters.blender_assets import selected_asset_keys

        keys = selected_asset_keys(context)
        if len(keys) < 2:
            self.report({"WARNING"}, "Select at least two assets to clone tags")
            return {"CANCELLED"}
        source = props.clone_source.strip()
        if not source:
            # Default: first selected asset is the source.
            source_token = keys[0].token
        else:
            source_token = source
        if source_token not in {k.token for k in keys}:
            self.report({"WARNING"}, "Source asset is not in the current selection")
            return {"CANCELLED"}
        # Build tag index for the source asset from its snapshot if available,
        # otherwise from the live Asset Representation metadata.
        from ..adapters.blender_assets import selected_assets

        source_tags: list[str] = []
        for asset in selected_assets(context):
            key = _target_key(asset, context)
            if key == source_token:
                source_tags = [tag.name for tag in asset.metadata.tags]
                break
        if not source_tags:
            desired_state = SESSION.desired.get(source_token)
            source_tags = list(desired_state.after) if desired_state else []
        if not source_tags:
            self.report({"WARNING"}, "Source asset has no tags to clone")
            return {"CANCELLED"}
        targets = [k.token for k in keys if k.token != source_token]
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


def _target_key(asset, context) -> str:
    from ..core.models import AssetKey

    fallback = getattr(context, "asset_library_reference", "") or ""
    owner = getattr(asset, "owner_asset_library", None)
    lib = getattr(owner, "name", "") or fallback
    path = str(getattr(asset, "full_library_path", "") or "")
    return AssetKey(
        library_reference=str(lib),
        blend_path=path,
        id_type=str(getattr(asset, "id_type", "")).upper(),
        datablock_name=str(asset.name),
    ).token


class BATM_OT_manual_tag_page(bpy.types.Operator):
    """Change the pagination offset for the Tag list."""

    bl_idname = "batm.manual_tag_page"
    bl_label = "Tag Page"
    delta: IntProperty()

    def execute(self, context):
        props = context.window_manager.batm_runtime
        _asset_total, frequency = selected_tag_frequency(context)
        page_size = max(1, props.manual_page_size)
        page_count = max(1, math.ceil(max(1, len(frequency)) / page_size))
        props.manual_tag_page = max(0, min(props.manual_tag_page + self.delta, page_count - 1))
        return {"FINISHED"}


CLASSES = (
    BATM_OT_manual_add,
    BATM_OT_toggle_tag_selection,
    BATM_OT_manual_remove,
    BATM_OT_manual_replace,
    BATM_OT_clear_pending,
    BATM_OT_manual_select_by_tag,
    BATM_OT_manual_clone_selected,
    BATM_OT_manual_tag_page,
)
