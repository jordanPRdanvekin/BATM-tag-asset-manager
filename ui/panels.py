"""Asset Browser sidebar UI for BATM."""

from __future__ import annotations

import math

import bpy

from ..adapters.blender_assets import (
    active_library_label,
    batm_preferences,
    selected_assets,
    selected_tag_frequency,
)
from ..core.session import SESSION, filter_tag_list, filtered_review_states
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


def _metrics_row(box, label: str, value: str, icon: str = "NONE") -> None:
    """Draw a single label/value row in the metrics table."""
    row = box.split(factor=0.6, align=False)
    row.label(text=label, icon=icon)
    row.label(text=value)


def _draw_metrics(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    if not _collapsible_header(box, props, "metrics_expanded", "Current Library"):
        return
    library_label = active_library_label(context)
    # The library name is information, not a disclosure control.
    row = box.row()
    row.label(text=library_label, icon="ASSET_MANAGER")
    library_count = INVENTORY.count_for_reference(library_label)
    if library_count is not None:
        _metrics_row(box, "Assets", f"{library_count:,}", "ASSET_MANAGER")
    elif INVENTORY.libraries:
        _metrics_row(box, "Assets", "not counted (Essentials/online)", "INFO")
    else:
        _metrics_row(box, "Assets", INVENTORY.status, "INFO")
    _metrics_row(box, "Selected", f"{len(selected_assets(context)):,}", "RESTRICT_SELECT_OFF")
    catalog = _current_catalog_label(context)
    if catalog:
        _metrics_row(box, "Catalog", catalog, "FILTER")
    if "Scanning" in INVENTORY.status:
        box.label(text=INVENTORY.status, icon="TIME")


def _draw_progress(layout, context) -> None:
    props = context.window_manager.batm_runtime
    box = layout.box()
    box.label(text=props.phase.replace("_", " ").title(), icon="TIME")
    box.label(text=f"Progress {props.progress * 100:.0f}%", icon="TIME")
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
    if not _collapsible_header(box, props, "manual_expanded", "Manual Tag Editor"):
        return

    # Search by name + filter by nº characters (both respect pagination).
    row = box.row(align=True)
    row.prop(props, "tag_search", text="", icon="VIEWZOOM")
    row.label(text="Filter by name", icon="SORTALPHA")
    row = box.row(align=True)
    row.prop(props, "tag_length_filter", text="Nº chars")
    row.label(text="Filter by character count (0 = all)", icon="FONT_DATA")

    # Tag list with multi-select (manual search + exact character-length filter).
    total, frequency = selected_tag_frequency(context)
    filtered = filter_tag_list(
        frequency,
        search=props.tag_search,
        length=props.tag_length_filter,
    )
    box.label(text=f"Tags ({len(filtered)} of {len(frequency)} total)", icon="OUTLINER_OB_GROUP_INSTANCE")
    page_size = max(1, int(props.manual_page_size))
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
    # Page navigation: first / previous / next / last.
    nav = box.row(align=True)
    nav.operator("batm.manual_tag_page", text="", icon="REW").action = "FIRST"
    nav.operator("batm.manual_tag_page", text="", icon="TRIA_LEFT").action = "PREV"
    nav.label(text=f"Page {page + 1} / {page_count} — {len(filtered)} Tags")
    nav.operator("batm.manual_tag_page", text="", icon="TRIA_RIGHT").action = "NEXT"
    nav.operator("batm.manual_tag_page", text="", icon="FF").action = "LAST"
    size_row = box.row(align=True)
    size_row.label(text="Tags per page")
    size_row.prop(props, "manual_page_size", text="", expand=True)

    # Select All / Clear Selection.
    select_row = box.row(align=True)
    select_row.operator("batm.manual_select_all", text="Select All", icon="CHECKBOX_HLT")
    select_row.operator("batm.manual_clear_selection", text="Clear", icon="X")

    box.separator()

    # Actions: Add / Remove / Replace as a single action selector.
    box.label(text="Actions", icon="TOOL_SETTINGS")
    box.prop(props, "manual_action", text="")

    action = props.manual_action
    if action == "ADD":
        row = box.row(align=True)
        row.prop(props, "manual_add", text="New Tags")
        row.operator("batm.manual_add", text="Add", icon="ADD")
    elif action == "REMOVE":
        selected_count = len(SESSION.selected_tags)
        box.label(text=f"Tags selected: {selected_count}")
        remove_row = box.row(align=True)
        remove_row.enabled = selected_count > 0
        remove_row.operator("batm.manual_remove", text="Remove Selected", icon="REMOVE")
    elif action == "REPLACE":
        selected_count = len(SESSION.selected_tags)
        box.label(text=f"Tags selected to replace: {selected_count}", icon="CHECKBOX_HLT")
        repl = box.row(align=True)
        repl.enabled = selected_count > 0
        repl.prop(props, "replace_destination", text="Replace selected with")
        box.operator("batm.manual_replace", text="Replace Selected", icon="FILE_REFRESH")

    box.separator()
    box.operator("batm.manual_clone_selected", text="Clone Selected", icon="DUPLICATE")
    box.prop(props, "clone_source")

    # Pending operations: cancel individual / mass entries from Add and Remove queues.
    box.separator()
    pending = box.box()
    head = pending.row(align=True)
    head.label(text=f"Pending Operations: {len(SESSION.operations)}", icon="TIME")
    if SESSION.operations:
        adds = [op for op in SESSION.operations if op.kind == "ADD"]
        removes = [op for op in SESSION.operations if op.kind == "REMOVE"]
        if adds:
            row = pending.row(align=True)
            row.label(text=f"Add ({len(adds)})", icon="ADD")
            row.operator("batm.pending_clear_kind", text="Clear Adds", icon="X").kind = "ADD"
            for op in adds[:10]:
                for value in op.values[:12]:
                    item = pending.row(align=True)
                    item.label(text=f"+ {value}  ({len(op.targets)} assets)")
                    cancel = item.operator("batm.pending_cancel", text="", icon="X")
                    cancel.operation_id = op.operation_id
            if len(adds) > 10 or any(len(op.values) > 12 for op in adds):
                pending.label(text="Showing first entries; use Clear to remove the rest", icon="INFO")
        if removes:
            row = pending.row(align=True)
            row.label(text=f"Remove ({len(removes)})", icon="REMOVE")
            row.operator("batm.pending_clear_kind", text="Clear Removes", icon="X").kind = "REMOVE"
            for op in removes[:10]:
                for value in op.values[:12]:
                    item = pending.row(align=True)
                    item.label(text=f"- {value}  ({len(op.targets)} assets)")
                    cancel = item.operator("batm.pending_cancel", text="", icon="X")
                    cancel.operation_id = op.operation_id
            if len(removes) > 10 or any(len(op.values) > 12 for op in removes):
                pending.label(text="Showing first entries; use Clear to remove the rest", icon="INFO")
        pending.operator("batm.clear_pending", text="Clear All Pending", icon="TRASH")
    else:
        pending.label(text="No pending operations", icon="INFO")


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
        text="Cleanup Rules",
        icon="TRIA_DOWN" if props.sanitize_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not props.sanitize_expanded:
        return
    box.label(text="Tag normalization applied before Preview", icon="INFO")
    box.label(text="Basic", icon="DOT")
    box.prop(props, "sanitize_separators", text="Separators")
    box.prop(props, "sanitize_recombine", text="Tag Handling")
    box.prop(props, "sanitize_casing", text="Casing")
    box.prop(props, "sanitize_segment", text="Split Compound Words")
    box.prop(props, "sanitize_sort", text="Sort Alphabetically")
    box.prop(props, "sanitize_max_tags", text="Max Tags per Asset")
    row = box.row()
    row.prop(
        props,
        "sanitize_advanced_expanded",
        text="Advanced",
        icon="TRIA_DOWN" if props.sanitize_advanced_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if props.sanitize_advanced_expanded:
        box.prop(props, "sanitize_max_length", text="Max Tag Length (63 max)")
        box.prop(props, "sanitize_remove_numbers", text="Remove Trailing Numbers")
        box.prop(props, "sanitize_merge_synonyms", text="Merge Synonyms")
        box.prop(props, "sanitize_blacklist", text="Blacklist (comma separated)")


def _draw_review(layout, context) -> None:
    from .review import draw_review
    draw_review(layout, context)

def _draw_recovery(layout, context) -> None:
    """Always-visible safety block: recoverable backups and recent errors.

    Recovery actions (Restore / Discard) stay one click away in every state;
    they are safety operations, not technical detail, so they are never hidden
    behind the collapsed Settings section.
    """
    backups = SESSION.recoverable_backup_paths
    errors = [event for event in SESSION.messages if event.get("severity") == "ERROR"]
    warnings = [event for event in SESSION.messages if event.get("severity") == "WARNING"]
    if not errors and not backups:
        return
    box = layout.box()
    box.label(text="Attention Needed", icon="ERROR")
    box.label(text=f"Recoverable backups: {len(backups)}   Errors: {len(errors)}", icon="RECOVER_LAST")
    if backups:
        row = box.row(align=True)
        restore = row.operator("batm.restore_backup", text="Restore Latest", icon="RECOVER_LAST")
        restore.filepath = backups[-1]
        discard = row.operator("batm.discard_backup", text="Discard Latest", icon="TRASH")
        discard.filepath = backups[-1]
    for event in (errors + warnings)[-3:]:
        box.label(text=f"{event.get('code')}: {event.get('message')}", icon="ERROR")


def _draw_technical(layout, context) -> None:
    """Technical diagnostics — kept inside Settings so they never dominate the UI."""
    props = context.window_manager.batm_runtime
    box = layout.box()
    row = box.row()
    row.prop(
        props,
        "diagnostics_expanded",
        text="Technical Details",
        icon="TRIA_DOWN" if props.diagnostics_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if not props.diagnostics_expanded:
        return
    errors = [event for event in SESSION.messages if event.get("severity") == "ERROR"]
    warnings = [event for event in SESSION.messages if event.get("severity") == "WARNING"]
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
    rowc = box.row(align=True)
    rowc.operator("batm.export_diagnostics", text="Export Report", icon="EXPORT")
    rowc.operator("batm.export_log_text", text="Export Log", icon="TEXT")
    rowc.operator("batm.refresh_all", text="Refresh", icon="FILE_REFRESH")


def _draw_settings(layout, context) -> None:
    """Settings section: Manual & Dictionary, Sanitizer and Technical Details."""
    props = context.window_manager.batm_runtime
    settings = layout.box()
    if not _collapsible_header(settings, props, "settings_expanded", "Settings"):
        return
    settings.label(text="Manual & Dictionary", icon="BOOKMARKS")
    _draw_rules(settings, context)
    settings.separator()
    settings.label(text="Sanitizer", icon="MODIFIER")
    _draw_sanitize(settings, context)
    settings.separator()
    _draw_technical(settings, context)


def _draw_run(layout, props) -> None:
    """RUN BATM — always visible, wide, large and never collapsible."""
    layout.scale_y = 3.0
    layout.operator(
        "batm.run",
        text="Run BATM  —  Analyze, Review & Apply Tags",
        icon="PLAY",
    )
    layout.scale_y = 1.0
    layout.label(
        text="Scans selected assets, proposes tags (AutoTag), then Review before writing.",
        icon="INFO",
    )
    if props.last_summary:
        layout.label(text=props.last_summary, icon="CHECKMARK")
    if props.status:
        layout.label(text=props.status)
    # Sponsored footer — minimal, non-invasive
    row = layout.row()
    row.alignment = "CENTER"
    row.label(text="Sponsored by b-water Studios Animation", icon="FUND")


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
        # 1. METRICS — collapsible information box, just above the main action.
        _draw_metrics(layout, context)
        if SESSION.phase in {"ANALYZING", "BACKING_UP", "BACKED_UP", "EXECUTING", "RESTORING", "REFRESHING"}:
            _draw_progress(layout, context)
        elif SESSION.phase == "REVIEW_READY":
            _draw_review(layout, context)
        else:
            # 2. RUN BATM — always visible, wide, large, never collapsible.
            _draw_run(layout, props)
            # 3. TAG EDITOR — directly below the main action.
            _draw_manual(layout, context)
            # 4. SETTINGS — Manual & Dictionary / Sanitizer / Technical Details.
            _draw_settings(layout, context)
        # Recovery / errors — always-visible safety block in every state.
        _draw_recovery(layout, context)


CLASSES = (BATM_PT_main,)