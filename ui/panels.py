"""Asset Browser sidebar UI for BATM."""

from __future__ import annotations

import math
from pathlib import Path

import bpy

from ..adapters.blender_assets import (
    active_library_label,
    batm_preferences,
    selected_assets,
    selected_tag_frequency,
)
from ..core.session import SESSION, filtered_review_states
from ..engine.backup import recoverable_backups
from ..engine.inventory import INVENTORY
from ..engine.diagnostics import summarize


def _current_catalog_label(context) -> str:
    params = getattr(getattr(context, "space_data", None), "params", None)
    if params is None:
        return ""
    visibility = getattr(params, "asset_catalog_visibility", "")
    if isinstance(visibility, str):
        return visibility
    return str(visibility or "")


def _current_filter_label(context) -> str:
    params = getattr(getattr(context, "space_data", None), "params", None)
    if params is None:
        return ""
    search = getattr(params, "filter_search", "") or ""
    asset_types = getattr(params, "filter_asset_types", 0) or 0
    parts = []
    if search:
        parts.append(f'"{search}"')
    if asset_types:
        parts.append("Types filtered")
    return ", ".join(parts) if parts else "None"


def _draw_metrics(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    if not _collapsible_header(box, props, "metrics_expanded", "Current Library"):
        return
    row = box.row()
    row.operator("batm.refresh_inventory", text="", icon="FILE_REFRESH")
    library_label = active_library_label(context)
    box.label(text=library_label, icon="DISCLOSURE_TRI_DOWN")
    library_count = INVENTORY.count_for_reference(library_label)
    if library_count is not None:
        box.label(text=f"Assets on Library: {library_count:,}")
    elif INVENTORY.libraries:
        box.label(text="Assets on Library: not counted (Essentials/online)")
    else:
        box.label(text=f"Assets on Library: {INVENTORY.status}")
    box.label(text=f"Selected: {len(selected_assets(context)):,}")
    catalog = _current_catalog_label(context)
    if catalog:
        box.label(text=f"Current Catalog: {catalog}")
    box.prop(props, "asset_type_filter", text="Asset Type Filter")
    filter_label = _current_filter_label(context)
    if filter_label:
        box.label(text=f"Filter: {filter_label}")
    if "Scanning" in INVENTORY.status:
        box.label(text=INVENTORY.status, icon="TIME")


def _draw_progress(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    box.label(text=props.phase.replace("_", " ").title(), icon="TIME")
    row = box.row()
    row.enabled = False
    row.prop(props, "progress", text=f"Progress {props.progress * 100:.0f}%", slider=True)
    if props.current_asset:
        box.label(text=f"Asset: {props.current_asset}", icon="OBJECT_DATA")
    if props.current_file:
        box.label(text=f"File: {props.current_file}", icon="FILE_BLEND")
    if props.task_label:
        box.label(text=f"Stage: {props.task_label}", icon="MODIFIER")
    box.label(text=props.status)
    if props.progress_detail:
        box.label(text=props.progress_detail)
    elapsed = int(props.elapsed_seconds)
    if props.eta_reliable and props.eta_seconds > 0.0:
        eta = int(props.eta_seconds)
        box.label(text=f"Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}  ETA {eta // 60:02d}:{eta % 60:02d}")
    else:
        box.label(text=f"Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}  ETA —")
    if SESSION.phase == "EXECUTING":
        box.operator("batm.cancel_execution", icon="CANCEL")


def _collapsible_header(box, props, prop_name: str, label: str) -> bool:
    """Draw a collapsible section header. Returns True when expanded."""
    is_open = getattr(props, prop_name)
    box.prop(
        props,
        prop_name,
        text=label,
        icon="TRIA_DOWN" if is_open else "TRIA_RIGHT",
        emboss=False,
    )
    return is_open


def _draw_manual(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    box.label(text="Manual Tag Editor", icon="ASSET_MANAGER")

    total, frequency = selected_tag_frequency(context)
    box.prop(props, "tag_search", text="", icon="VIEWZOOM")
    search = props.tag_search.casefold().strip()
    filtered = [item for item in frequency if not search or search in item[0].casefold()]
    box.label(text=f"Tags ({len(filtered)} of {len(frequency)} total)", icon="OUTLINER_OB_GROUP_INSTANCE")
    page_size = max(1, props.manual_page_size)
    page_count = max(1, math.ceil(max(1, len(filtered)) / page_size))
    page = min(props.manual_tag_page, page_count - 1)
    start = page * page_size
    for name, count in filtered[start : start + page_size]:
        selected = name.casefold() in SESSION.selected_tags
        row = box.row(align=True)
        toggle = row.operator(
            "batm.toggle_tag_selection",
            text="",
            icon="CHECKBOX_HLT" if selected else "CHECKBOX_DEHLT",
            emboss=False,
        )
        toggle.tag_name = name
        row.label(text=name)
        row.label(text=f"{count}/{total}")
    nav = box.row(align=True)
    nav.operator("batm.manual_tag_page", text="", icon="TRIA_LEFT").delta = -1
    nav.label(text=f"Page {page + 1} / {page_count} — {len(filtered)} Tags")
    nav.operator("batm.manual_tag_page", text="", icon="TRIA_RIGHT").delta = 1

    box.separator()
    row = box.row(align=True)
    row.prop(props, "manual_add", text="New Tags")
    row.operator("batm.manual_add", text="Add", icon="ADD")
    box.operator("batm.manual_remove", text="Remove Selected", icon="REMOVE")
    box.separator()
    box.label(text="Replace / Merge", icon="FILE_REFRESH")
    box.prop(props, "replace_source")
    box.prop(props, "replace_destination")
    box.operator("batm.manual_replace", text="Queue Replace / Merge", icon="FILE_REFRESH")
    box.separator()
    box.operator("batm.manual_clone_selected", text="Clone Selected", icon="DUPLICATE")
    box.prop(props, "clone_source")
    box.label(text=f"Pending Operations: {len(SESSION.operations)}")
    box.operator("batm.clear_pending", text="Clear Pending Queue", icon="TRASH")


def _draw_rules(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    row = box.row()
    row.prop(
        props,
        "show_rules",
        text="AutoTag Rules",
        icon="TRIA_DOWN" if props.show_rules else "TRIA_RIGHT",
        emboss=False,
    )
    if not props.show_rules:
        return
    box.prop(props, "rule_search", text="", icon="VIEWZOOM")
    search = props.rule_search.casefold().strip()
    shown = 0
    for index, rule in enumerate(SESSION.rules):
        if search and search not in rule.name.casefold():
            continue
        if shown >= 30:
            break
        row = box.row(align=True)
        toggle = row.operator(
            "batm.rule_toggle",
            text="",
            icon="CHECKBOX_HLT" if rule.enabled else "CHECKBOX_DEHLT",
            emboss=False,
        )
        toggle.index = index
        edit = row.operator("batm.rule_edit", text=f"{rule.priority}: {rule.name}", icon="GREASEPENCIL")
        edit.index = index
        duplicate = row.operator("batm.rule_duplicate", text="", icon="DUPLICATE")
        duplicate.index = index
        delete = row.operator("batm.rule_delete", text="", icon="X")
        delete.index = index
        shown += 1
    controls = box.row(align=True)
    controls.operator("batm.rule_edit", text="Add Rule", icon="ADD").index = -1
    controls.operator("batm.rules_import", text="Import", icon="IMPORT")
    controls.operator("batm.rules_export", text="Export", icon="EXPORT")
    box.operator("batm.rules_reset", text="Restore Bundled Rules", icon="LOOP_BACK")


def _draw_sanitize(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    row = box.row()
    row.prop(
        props,
        "sanitize_expanded",
        text="Sanitize Rules",
        icon="TRIA_DOWN" if props.sanitize_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not props.sanitize_expanded:
        return
    box.label(text="Tag normalization applied before Preview", icon="INFO")
    box.prop(props, "sanitize_separators", text="Separators")
    box.prop(props, "sanitize_casing", text="Casing")
    box.prop(props, "sanitize_sort", text="Sort Alphabetically")
    box.prop(props, "sanitize_max_length", text="Max Tag Length")
    box.prop(props, "sanitize_remove_numbers", text="Remove Trailing Numbers")
    box.prop(props, "sanitize_merge_synonyms", text="Merge Synonyms")
    box.prop(props, "sanitize_blacklist", text="Blacklist (comma separated)")


def _draw_review(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    box.label(text="Review", icon="PREVIEW_RANGE")
    all_states = list(SESSION.desired.values())
    changed = [state for state in all_states if state.changed]
    invalid = [state for state in changed if not state.valid]
    with_warnings = [state for state in all_states if state.warnings]
    summary = layout.box()
    summary.label(
        text=(
            f"{len(all_states)} assets — +{sum(len(state.added) for state in changed)} added "
            f"/ -{sum(len(state.removed) for state in changed)} removed"
        )
    )
    summary.label(
        text=f"{len(changed)} changed  {len(all_states) - len(changed)} unchanged  {len(with_warnings)} warnings  {len(invalid)} invalid"
    )
    row = box.row(align=True)
    row.prop(props, "review_filter", text="")
    row.prop(props, "review_search", text="", icon="VIEWZOOM")
    states = filtered_review_states(props.review_search, props.review_filter)
    prefs = batm_preferences(context)
    page_size = int(getattr(prefs, "review_page_size", 20))
    page_count = max(1, math.ceil(len(states) / page_size))
    page = min(props.review_page, page_count - 1)
    start = page * page_size
    for state in states[start : start + page_size]:
        row = box.row(align=True)
        toggle = row.operator(
            "batm.review_toggle_asset",
            text="",
            icon="CHECKBOX_HLT" if state.enabled else "CHECKBOX_DEHLT",
            emboss=False,
        )
        toggle.token = state.key.token
        select = row.operator(
            "batm.review_select",
            text=f"{state.key.datablock_name} [{state.key.id_type}]" + ("" if state.enabled else " — OFF"),
            icon="ERROR" if state.warnings else ("DECORATE_KEYFRAME" if state.changed else "CHECKMARK"),
        )
        select.token = state.key.token
        row.label(text=f"+{len(state.added)} / -{len(state.removed)}")
    if not states:
        box.label(text="No assets match the current search and filter", icon="INFO")
    navigation = box.row(align=True)
    navigation.operator("batm.review_page", text="", icon="TRIA_LEFT").delta = -1
    navigation.label(text=f"Page {page + 1} / {page_count} — {len(states)} assets")
    navigation.operator("batm.review_page", text="", icon="TRIA_RIGHT").delta = 1

    active = SESSION.desired.get(SESSION.active_review_token)
    if active:
        detail = layout.box()
        detail.label(text=f"{active.key.datablock_name} — {active.key.id_type}", icon="ASSET_MANAGER")
        detail.label(text=Path(active.key.blend_path).name or "Current File")
        detail.label(text="Before: " + (", ".join(active.before) or "(none)"))
        detail.label(text="After:")
        for tag in active.after:
            row = detail.row(align=True)
            reasons = active.tag_reasons.get(tag.casefold(), [])
            reason_text = f" ({reasons[0]})" if len(reasons) == 1 else f" ({len(reasons)} reasons)" if reasons else ""
            row.label(text=tag + reason_text)
            remove = row.operator("batm.review_remove_tag", text="", icon="X")
            remove.token = active.key.token
            remove.value = tag
        detail.operator("batm.review_add_tag", text="Add Tag", icon="ADD").token = active.key.token
        for warning in active.warnings:
            detail.label(text=warning, icon="ERROR")
        for explanation in active.explanations[:8]:
            detail.label(text=explanation, icon="INFO")

        operations = [
            (index, operation)
            for index, operation in enumerate(SESSION.operations)
            if active.key.token in operation.targets
        ]
        if operations:
            detail.label(text="Operations:")
            for index, operation in operations[:12]:
                row = detail.row(align=True)
                toggle = row.operator(
                    "batm.review_toggle_operation",
                    text="",
                    icon="CHECKBOX_HLT" if operation.enabled else "CHECKBOX_DEHLT",
                    emboss=False,
                )
                toggle.index = index
                row.label(text=f"{operation.origin}: {operation.kind} — {operation.explanation}")

    changed = [state for state in SESSION.desired.values() if state.changed]
    invalid = [state for state in changed if not state.valid]
    controls = layout.row(align=True)
    controls.operator("batm.review_cancel", text="Discard Review", icon="TRASH")
    confirm = controls.row(align=True)
    confirm.enabled = bool(changed) and not invalid
    confirm.operator("batm.execute", text="Confirm and Apply", icon="CHECKMARK")
    if invalid:
        layout.label(text=f"Resolve {len(invalid)} invalid assets before confirming", icon="ERROR")


def _draw_diagnostics(layout, context) -> None:
    props = context.window_manager.batm_runtime
    backups = recoverable_backups()
    errors = [event for event in SESSION.messages if event.get("severity") == "ERROR"]
    warnings = [event for event in SESSION.messages if event.get("severity") == "WARNING"]
    expanded = props.diagnostics_expanded or bool(errors) or bool(backups)
    box = layout.box()
    row = box.row()
    row.prop(
        props,
        "diagnostics_expanded",
        text="Diagnostics & Logs",
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not expanded:
        return
    library_label = active_library_label(context)
    blend_files = INVENTORY.blend_files_for(library_label)
    if blend_files is not None:
        box.label(text=f"Blend Files: {blend_files:,}", icon="FILE_BLEND")
    box.label(text=f"Last Scan: {INVENTORY.last_scan or 'Never'}", icon="TIME")
    summary = summarize(SESSION.snapshots.values(), SESSION.operations)
    box.label(text=f"Tags: {summary['total_tags']} total / {summary['unique_tags']} unique")
    box.label(
        text=(
            f"Duplicates: {summary['duplicate_tags']}  Empty: {summary['empty_tags']}  "
            f"Invalid: {summary['invalid_tags']}"
        )
    )
    box.label(text=f"Assets requiring cleaning: {summary['assets_requiring_cleaning']}")
    box.label(text=f"Pending operations: {summary['pending_operations']}")
    box.label(text=f"Warnings: {len(warnings)}   Errors: {len(errors)}")
    for event in (errors + warnings)[-8:]:
        box.label(text=f"{event.get('code')}: {event.get('message')}", icon="ERROR")
    if backups:
        box.label(text=f"Recoverable Backups: {len(backups)}", icon="RECOVER_LAST")
        latest = backups[-1]
        row = box.row(align=True)
        restore = row.operator("batm.restore_backup", text="Restore Latest", icon="RECOVER_LAST")
        restore.filepath = str(latest)
        discard = row.operator("batm.discard_backup", text="Discard", icon="TRASH")
        discard.filepath = str(latest)
    row = box.row(align=True)
    row.operator("batm.export_diagnostics", text="Export Report", icon="EXPORT")
    row.operator("batm.export_log_text", text="Export Log", icon="TEXT")
    row.operator("batm.refresh_browser", text="Refresh Browser", icon="FILE_REFRESH")


class BATM_PT_main(bpy.types.Panel):
    bl_label = "BATM"
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOLS"
    bl_category = "BATM"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "browse_mode", "") == "ASSETS"

    def draw(self, context):
        layout = self.layout
        props = context.window_manager.batm_runtime
        _draw_metrics(layout, context)
        if SESSION.phase in {"ANALYZING", "BACKING_UP", "BACKED_UP", "EXECUTING", "RESTORING", "REFRESHING"}:
            _draw_progress(layout, context)
        elif SESSION.phase == "REVIEW_READY":
            _draw_review(layout, context)
        else:
            # Run BATM (collapsible)
            run = layout.box()
            if _collapsible_header(run, props, "run_expanded", "Run BATM"):
                run.operator("batm.run", text="Analyze and Review", icon="PLAY")
                run.label(text=props.status)
            # Manual Tag Editor
            _draw_manual(layout, context)
            # Settings (collapsible): AutoTag Rules + Sanitize Rules
            settings = layout.box()
            if _collapsible_header(settings, props, "settings_expanded", "Settings"):
                _draw_rules(settings, context)
                _draw_sanitize(settings, context)
        _draw_diagnostics(layout, context)


CLASSES = (BATM_PT_main,)
