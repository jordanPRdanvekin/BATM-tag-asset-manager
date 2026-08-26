"""Guided step-by-step Review UI (native UILayout only)."""
from __future__ import annotations
import math
from ..adapters.blender_assets import batm_preferences
from ..core.session import SESSION, filtered_added_tags, filtered_final_tags, filtered_review_states


def _aggregate_before(states):
    """All existing Tags (from ``before``) with coverage and removal state."""
    agg = {}
    for state in states:
        removed_keys = {tag.casefold() for tag in state.removed}
        for tag in state.before:
            cf = tag.casefold()
            display, count, removing = agg.get(cf, (tag, 0, False))
            agg[cf] = (display, count + 1, removing or cf in removed_keys)
    return sorted(agg.values(), key=lambda item: item[0].casefold())


def draw_review(layout, context):
    props = context.window_manager.batm_runtime
    all_states = list(SESSION.desired.values())
    changed = [s for s in all_states if s.changed]
    invalid = [s for s in changed if not s.valid]
    with_warnings = sum(1 for s in all_states if s.warnings)
    box = layout.box()
    box.label(text="Review", icon="PREVIEW_RANGE")
    summary = layout.box()
    summary.label(text=(
        f"{len(all_states)} assets · +{sum(len(s.added) for s in changed)} added "
        f"/ -{sum(len(s.removed) for s in changed)} removed"
    ))
    summary.label(text=(f"{len(changed)} changed  {len(all_states)-len(changed)} unchanged  "
                        f"{with_warnings} warnings  {len(invalid)} invalid"))
    _draw_steps(layout, props)
    step = props.review_step
    if step == "ADD":
        _step_added(layout, context, props, all_states)
    elif step == "REMOVE":
        _step_removed(layout, all_states)
    elif step == "SANITIZE":
        _step_sanitize(layout, props)
    else:
        _step_assets(layout, context, props, all_states)
    _draw_confirm(layout, changed, invalid, props)


def _draw_steps(layout, props):
    steps = [
        ("ADD", "Add"),
        ("REMOVE", "Remove"),
        ("SANITIZE", "Cleanup"),
        ("ASSETS", "Last Step"),
    ]
    row = layout.row(align=True)
    for key, label in steps:
        op = row.operator("batm.review_step", text=label, depress=(props.review_step == key))
        op.value = key
    # Per-step activation: each step can be on or off independently. Enable All /
    # Disable All switch every step at once (they do not touch individual assets).
    toggles = layout.row(align=True)
    toggles.label(text="Apply:", icon="CHECKBOX_HLT")
    toggles.prop(props, "review_add_on", text="Add", toggle=True)
    toggles.prop(props, "review_remove_on", text="Remove", toggle=True)
    toggles.prop(props, "review_cleanup_on", text="Cleanup", toggle=True)
    toggles.prop(props, "review_assets_on", text="Last Step", toggle=True)
    all_row = layout.row(align=True)
    all_row.operator("batm.review_enable_all", text="Enable All", icon="CHECKBOX_HLT")
    all_row.operator("batm.review_disable_all", text="Disable All", icon="CHECKBOX_DEHLT")


def _step_added(layout, context, props, states):
    agg = filtered_added_tags(
        states,
        search=props.review_add_search,
        length=props.tag_length_filter,
    )
    box = layout.box()
    # Filters: free-text ("manual") search plus an exact character-length filter.
    filter_row = box.row(align=True)
    filter_row.prop(props, "review_add_search", text="", icon="VIEWZOOM")
    filter_row.prop(props, "tag_length_filter", text="Length")
    box.label(text=f"Add · {len(agg)} unique Tags", icon="ADD")
    if not agg:
        box.label(text="No additions proposed", icon="INFO")
        return
    # Paginated list: 10 Tags per page by default, switchable to 100.
    page_size = max(1, int(props.review_add_page_size))
    page_count = max(1, math.ceil(len(agg) / page_size))
    page = min(props.review_add_page, page_count - 1)
    start = page * page_size
    for display, count in agg[start:start + page_size]:
        row = box.row(align=True)
        # Double-click the Tag text to rewrite it; the pencil opens on one click.
        tag_btn = row.operator("batm.review_add_tag_edit", text=display, emboss=False)
        tag_btn.value = display
        tag_btn.force = False
        row.label(text=f"{count}/{len(states)}")
        edit = row.operator("batm.review_add_tag_edit", text="", icon="GREASEPENCIL")
        edit.value = display
        edit.force = True
        remove = row.operator("batm.review_remove_added", text="", icon="X")
        remove.value = display
    # Page navigation: first / previous / next / last.
    nav = box.row(align=True)
    nav.operator("batm.review_add_page", text="", icon="REW").action = "FIRST"
    nav.operator("batm.review_add_page", text="", icon="TRIA_LEFT").action = "PREV"
    nav.label(text=f"Page {page + 1} / {page_count}")
    nav.operator("batm.review_add_page", text="", icon="TRIA_RIGHT").action = "NEXT"
    nav.operator("batm.review_add_page", text="", icon="FF").action = "LAST"
    size_row = box.row(align=True)
    size_row.label(text="Tags per page")
    size_row.prop(props, "review_add_page_size", text="", expand=True)


def _step_removed(layout, states):
    agg = _aggregate_before(states)
    box = layout.box()
    box.label(text=f"Remove · {len(agg)} existing Tags", icon="REMOVE")
    box.label(text="Use X to queue removing any Tag; proposed removals are shown as such.", icon="INFO")
    for display, count, removing in agg[:80]:
        row = box.row(align=True)
        row.label(text=(display + "  (proposed)" if removing else display), icon="REMOVE" if removing else "NONE")
        row.label(text=f"{count}/{len(states)}")
        op = row.operator("batm.review_remove_tag_value", text="", icon="X")
        op.value = display
    if not agg:
        box.label(text="No existing Tags on the selection", icon="INFO")


def _step_sanitize(layout, props):
    box = layout.box()
    box.label(text="Cleanup", icon="MODIFIER")
    box.label(text="Split Compound Words and Remove Trailing Numbers are on by default.", icon="INFO")
    box.prop(props, "sanitize_segment", text="Split Compound Words")
    box.prop(props, "sanitize_recombine", text="Compound Result (Both / Split / Combined)")
    box.prop(props, "sanitize_remove_numbers", text="Remove Trailing Numbers")
    box.prop(props, "sanitize_separators", text="Separators")
    box.prop(props, "sanitize_casing", text="Casing")
    box.prop(props, "sanitize_sort", text="Sort Alphabetically")
    box.prop(props, "sanitize_max_tags", text="Max Tags per Asset")
    box.label(text="Changes apply live to the Tags below.", icon="TIME")


def _step_assets(layout, context, props, states):
    box = layout.box()
    box.label(text="Last Step", icon="OUTLINER_OB_GROUP_INSTANCE")
    box.label(
        text="Final Tags preview below, then adjust individual assets before enabling and confirming.",
        icon="INFO",
    )
    # Filterable, paginated preview of every Tag that will end up applied.
    final = filtered_final_tags(
        states,
        search=props.review_add_search,
        length=props.tag_length_filter,
    )
    preview = layout.box()
    filter_row = preview.row(align=True)
    filter_row.prop(props, "review_add_search", text="", icon="VIEWZOOM")
    filter_row.prop(props, "tag_length_filter", text="Length")
    head = preview.row(align=True)
    head.label(text=f"Final Tags · {len(final)} unique", icon="CHECKMARK")
    head.label(text=f"over {len(states)} assets")
    if not final:
        preview.label(text="No tags will be applied", icon="INFO")
    else:
        page_size = max(1, int(props.review_add_page_size))
        page_count = max(1, math.ceil(len(final) / page_size))
        page = min(props.review_final_page, page_count - 1)
        start = page * page_size
        for display, count in final[start:start + page_size]:
            row = preview.row(align=True)
            row.label(text=display)
            row.label(text=f"{count}/{len(states)}")
        nav = preview.row(align=True)
        nav.operator("batm.review_final_page", text="", icon="REW").action = "FIRST"
        nav.operator("batm.review_final_page", text="", icon="TRIA_LEFT").action = "PREV"
        nav.label(text=f"Page {page + 1} / {page_count}")
        nav.operator("batm.review_final_page", text="", icon="TRIA_RIGHT").action = "NEXT"
        nav.operator("batm.review_final_page", text="", icon="FF").action = "LAST"
        size_row = preview.row(align=True)
        size_row.label(text="Tags per page")
        size_row.prop(props, "review_add_page_size", text="", expand=True)
    box = layout.box()
    box.label(text="Per-asset fine-tune", icon="OBJECT_DATA")
    row = box.row(align=True)
    row.prop(props, "review_filter", text="")
    row.prop(props, "review_search", text="", icon="VIEWZOOM")
    states = filtered_review_states(props.review_search, props.review_filter)
    prefs = batm_preferences(context)
    page_size = int(getattr(prefs, "review_page_size", 20))
    page_count = max(1, math.ceil(len(states) / page_size))
    page = min(props.review_page, page_count - 1)
    start = page * page_size
    for state in states[start:start + page_size]:
        r = box.row(align=True)
        toggle = r.operator(
            "batm.review_toggle_asset", text="",
            icon="CHECKBOX_HLT" if state.enabled else "CHECKBOX_DEHLT", emboss=False,
        )
        toggle.token = state.key.token
        select = r.operator(
            "batm.review_select",
            text=f"{state.key.datablock_name} [{state.key.id_type}]" + ("" if state.enabled else " · OFF"),
            icon="INFO" if state.warnings else ("DECORATE_KEYFRAME" if state.changed else "NONE"),
        )
        select.token = state.key.token
        r.label(text=f"+{len(state.added)} / -{len(state.removed)}")
    if not states:
        box.label(text="No assets match the search and filter", icon="INFO")
    nav = box.row(align=True)
    nav.operator("batm.review_page", text="", icon="TRIA_LEFT").delta = -1
    nav.label(text=f"Page {page + 1} / {page_count} · {len(states)} assets")
    nav.operator("batm.review_page", text="", icon="TRIA_RIGHT").delta = 1
    active = SESSION.desired.get(SESSION.active_review_token)
    if active is not None:
        detail = layout.box()
        detail.label(text=f"{active.key.datablock_name} [{active.key.id_type}]", icon="OBJECT_DATA")
        detail.label(text="Before: " + (", ".join(active.before) if active.before else "(none)"))
        detail.label(text="After:  " + (", ".join(active.after) if active.after else "(none)"))
        for tag in active.after:
            row = detail.row(align=True)
            reasons = active.tag_reasons.get(tag.casefold(), [])
            if len(reasons) == 1:
                suffix = f" ({reasons[0]})"
            elif reasons:
                suffix = f" ({len(reasons)} reasons)"
            else:
                suffix = ""
            row.label(text=tag + suffix)
            remove = row.operator("batm.review_remove_tag", text="", icon="X")
            remove.token = active.key.token
            remove.value = tag
        detail.operator("batm.review_add_tag", text="Add Tag", icon="ADD").token = active.key.token
        for warning in active.warnings:
            detail.label(text=warning, icon="INFO")
        for explanation in active.explanations[:8]:
            detail.label(text=explanation, icon="INFO")


def _draw_confirm(layout, changed, invalid, props):
    changed = [s for s in changed if s.enabled]
    invalid = [s for s in changed if not s.valid]
    controls = layout.row(align=True)
    controls.operator("batm.review_cancel", text="Discard Review", icon="TRASH")
    confirm = controls.row(align=True)
    confirm.enabled = bool(changed) and not invalid
    confirm.operator("batm.execute", text="Confirm and Apply", icon="CHECKMARK")
    apply_valid = controls.row(align=True)
    apply_valid.enabled = bool(changed) and bool(invalid)
    apply_valid.operator("batm.review_apply_valid", text="Apply Valid Only", icon="FILTER")
    if invalid:
        layout.label(text=f"Resolve {len(invalid)} invalid assets before confirming", icon="INFO")
