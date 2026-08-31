"""Blender RNA properties used only for UI and persistent preferences."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

ADDON_ID = "batch_asset_tag_manager"


class BATMPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    max_workers: IntProperty(
        name="Maximum Workers",
        description="Upper bound for adaptive Blender background processes",
        default=4,
        min=1,
        max=4,
    )
    worker_timeout_seconds: IntProperty(
        name="Worker Timeout (Seconds)",
        description="Seconds a background worker may run before it is killed. Applies per request (inventory, analysis, apply, restore)",
        default=2000,
        min=30,
        max=7200,
    )
    review_page_size: IntProperty(name="Review Page Size", default=20, min=5, max=100)
    log_retention_days: IntProperty(name="Log Retention (Days)", default=30, min=1, max=3650)
    log_retention_runs: IntProperty(name="Maximum Run Logs", default=50, min=1, max=1000)
    catalog_mode: EnumProperty(
        name="Catalog",
        items=[
            ("CONCEPTS", "Concepts", "Emits only primary domain concepts (compact)"),
            ("FULL", "Full", "Emits domain concepts and matched categories"),
        ],
        default="CONCEPTS",
    )
    cross_tagging: BoolProperty(name="Enable Cross-Tagging", default=True)
    autotag_split_compounds: BoolProperty(
        name="Split Compound Names",
        description="Emit the atomic components of glued names (rosarojavioleta -> Rosa, Roja, Violeta) together with the compound unit. On by default",
        default=True,
    )
    autotag_tag_style: EnumProperty(
        name="AutoTag Style",
        description="Naming policy applied to tags generated from names (Title Case is the historic preset and stays unchanged)",
        items=[
            ("TITLE", "Title Case", "Capitalize every word (e.g. Oak Tree)"),
            ("KEBAB", "Kebab Case", "Lowercase words joined with hyphens (e.g. oak-tree)"),
            ("SNAKE", "Snake Case", "Lowercase words joined with underscores (e.g. oak_tree)"),
            ("PASCAL", "Pascal Case", "Every word capitalized with no separator (e.g. OakTree)"),
            ("LOWER", "Lower Case", "All words lowercase (e.g. oak tree)"),
        ],
        default="TITLE",
    )
    taxonomy_expanded: BoolProperty(name="Internal Taxonomy Manual", default=False)
    taxonomy_domain: StringProperty(name="Filter Domain")
    taxonomy_search: StringProperty(name="Search Term")
    taxonomy_alias_term: StringProperty(name="Alias Term")
    taxonomy_alias_tags: StringProperty(name="Alias Tags")

    def draw(self, _context):
        layout = self.layout
        # Sponsored header — always visible, non-invasive
        header = layout.box()
        header.label(text="Sponsored by b-water Studios Animation", icon="FUND")
        header.operator("wm.url_open", text="b-waterstudios.com", icon="URL").url = "https://b-waterstudios.com"
        layout.prop(self, "max_workers")
        layout.prop(self, "worker_timeout_seconds")
        layout.prop(self, "review_page_size")
        layout.separator()
        layout.prop(self, "log_retention_days")
        layout.prop(self, "log_retention_runs")
        layout.separator()
        box = layout.box()
        box.label(text="AutoTag Settings", icon="PREFERENCES")
        box.prop(self, "catalog_mode", text="Catalog level")
        box.prop(self, "cross_tagging", text="Enable Cross-Tagging")
        box.prop(self, "autotag_split_compounds", text="Split Compound Names")
        box.prop(self, "autotag_tag_style", text="Tag naming style")
        row = box.row()
        row.prop(
            self,
            "taxonomy_expanded",
            text="Internal Taxonomy Manual",
            icon="TRIA_DOWN" if self.taxonomy_expanded else "TRIA_RIGHT",
            emboss=False,
        )
        if self.taxonomy_expanded:
            box.prop(self, "taxonomy_domain", text="Filter Domain")
            box.prop(self, "taxonomy_search", text="Search Term", icon="VIEWZOOM")
            if self.taxonomy_search.strip():
                _draw_taxonomy_hits(box, self.taxonomy_search, self.taxonomy_domain)
            box.separator()
            box.label(text="Custom Alias")
            box.prop(self, "taxonomy_alias_term", text="Term")
            box.prop(self, "taxonomy_alias_tags", text="Tags (comma separated)")
            box.operator("batm.taxonomy_add_alias", text="Add Custom Tag Alias", icon="ADD")
            row2 = box.row(align=True)
            row2.operator("batm.taxonomy_export", text="Export", icon="EXPORT")
            row2.operator("batm.taxonomy_import", text="Import", icon="IMPORT")


_RECOMPILING = False


def _live_recompile(self, context) -> None:
    """Recompute the Review from the current step/Cleanup options (real-time).

    This is the single guarded recompile path. It runs only during REVIEW_READY,
    so a change to a step toggle or a Cleanup option (Split / Combined / Remove
    Trailing Numbers, ...) updates the proposed Tags immediately. It never writes
    Blender assets, never creates a backup and never executes. The ``_RECOMPILING``
    guard collapses any short-lived property-update cascade into a single rebuild,
    so the same frame cannot trigger recursive or cascading recompiles.
    """
    global _RECOMPILING
    if _RECOMPILING:
        return
    if context is None or getattr(getattr(context, "window_manager", None), "batm_runtime", None) is None:
        return
    from ..core.sanitizer import options_from_props
    from ..core.session import SESSION

    if SESSION.phase != "REVIEW_READY":
        return
    _RECOMPILING = True
    try:
        SESSION.sanitize_options = options_from_props(self)
        SESSION.recompile_preserving_disabled()
        context.window_manager.batm_runtime.status = "Review updated"
    finally:
        _RECOMPILING = False


class BATMRuntimeProperties(bpy.types.PropertyGroup):
    phase: StringProperty(name="Phase", default="IDLE")
    status: StringProperty(name="Status", default="Ready")
    progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    elapsed_seconds: FloatProperty(name="Elapsed", default=0.0, min=0.0)
    eta_seconds: FloatProperty(name="ETA", default=0.0, min=0.0)
    eta_reliable: BoolProperty(
        name="ETA Reliable",
        description="True only while the current phase gives deterministic progress (worker batches)",
        default=False,
    )
    progress_detail: StringProperty(name="Progress Detail")
    current_asset: StringProperty(
        name="Current Asset",
        description="Asset currently being analyzed or written",
        default="",
    )
    current_file: StringProperty(
        name="Current File",
        description="Blend file currently being processed",
        default="",
    )
    task_label: StringProperty(
        name="Current Stage",
        description="Stage of the pipeline currently running (Analyzing, Applying, Restoring...)",
        default="",
    )
    manual_add: StringProperty(
        name="New Tags",
        description="Comma or semicolon separated Tags to queue as an ADD operation for all selected assets",
    )
    replace_source: StringProperty(
        name="Replace",
        description="Existing Tag to find and replace across the selected assets",
    )
    replace_destination: StringProperty(
        name="With",
        description="One or more comma-separated Tags to use as the replacement",
    )
    tag_search: StringProperty(
        name="Search Tags",
        description="Filter the Tag list by name. Toggle Tags to queue them for removal, replace or coverage browsing",
    )
    review_search: StringProperty(
        name="Search Review",
        description="Filter the Review list by asset name, ID type, blend path or proposed Tag",
    )
    review_page: IntProperty(name="Page", default=0, min=0)
    review_filter: EnumProperty(
        name="Review Filter",
        description="Filter the Review list by change state",
        items=[
            ("ALL", "All", "Show every asset"),
            ("CHANGED", "Changed", "Only assets with proposed tag changes"),
            ("UNCHANGED", "Unchanged", "Only assets without tag changes"),
            ("WARNINGS", "Warnings", "Only assets with warnings"),
            ("INVALID", "Invalid", "Only invalid assets blocking confirmation"),
        ],
        default="ALL",
    )
    # Tag character-length filter. 0 = show every Tag; any positive value keeps
    # only Tags with exactly that many letters/digits (spaces and symbols ignored).
    # Complements the free-text "manual" search (tag_search / review_add_search).
    tag_length_filter: IntProperty(
        name="Tag Length",
        description="Show only Tags with exactly this many letters (0 = show all)",
        default=0,
        min=0,
        max=100,
    )
    # Free-text (manual) filter for the Review Add step tags.
    review_add_search: StringProperty(
        name="Search Tags",
        description="Filter the proposed Add Tags by name (manual filter)",
    )
    # Pagination for the Review Add step: current page + page size (10 / 100).
    review_add_page: IntProperty(
        name="Add Page",
        description="Current page of the proposed Add Tags list",
        default=0,
        min=0,
    )
    review_add_page_size: EnumProperty(
        name="Tags Per Page",
        description="How many proposed Add Tags to show per page",
        items=[
            ("10", "10", "10 Tags per page"),
            ("100", "100", "100 Tags per page"),
        ],
        default="10",
    )
    # Pagination of the Final Tags preview (Review last step). The page size is
    # shared with the Add step (review_add_page_size); only the page index is
    # separate so switching steps never jumps the other list.
    review_final_page: IntProperty(
        name="Final Tags Page",
        description="Current page of the Final Tags preview",
        default=0,
        min=0,
    )
    rule_search: StringProperty(
        name="Search Rules",
        description="Filter AutoTag rules by name",
    )
    diagnostics_expanded: BoolProperty(
        name="Diagnostics & Logs",
        description="Show technical diagnostics, event log and recovery backups",
        default=False,
    )
    show_rules: BoolProperty(
        name="AutoTag Rules",
        description="Show and edit the declarative AutoTag rules",
        default=False,
    )
    last_log_path: StringProperty(name="Last Log")
    last_summary: StringProperty(
        name="Last Result",
        description="Summary of the most recent BATM operation (kept visible in the Run block)",
        default="",
    )

    # Collapsible top-level sections.
    metrics_expanded: BoolProperty(
        name="Current Library",
        description="Show the active library, asset count, blend files, selection, catalog and filter",
        default=True,
    )
    settings_expanded: BoolProperty(
        name="Settings",
        description="Configure AutoTag rules and Cleanup behaviour",
        default=False,
    )
    manual_expanded: BoolProperty(
        name="Manual Tag Editor",
        description="Show the Manual Tag Editor",
        default=True,
    )

    # Unified Manual Tag list pagination (single list, search + paging).
    manual_tag_page: IntProperty(name="Tag Page", default=0, min=0)
    manual_page_size: EnumProperty(
        name="Tags Per Page",
        description="How many Tags to show per page in the Manual Tag Editor",
        items=[
            ("10", "10", "10 Tags per page"),
            ("100", "100", "100 Tags per page"),
        ],
        default="10",
    )
    clone_source: StringProperty(
        name="Clone From",
        description="Asset whose Tags will be cloned to the rest of the selection. Leave empty to use the first selected asset",
    )
    manual_action: EnumProperty(
        name="Action",
        description="Manual Tag Editor action to configure",
        items=[
            ("ADD", "Add", "Add new Tags to the selected assets"),
            ("REMOVE", "Remove", "Remove the selected Tags from the selected assets"),
            ("REPLACE", "Replace", "Replace one Tag with one or more new Tags"),
        ],
        default="ADD",
    )

    # Cleanup Rules configuration.
    sanitize_expanded: BoolProperty(
        name="Cleanup Rules",
        description="Configure how Tags are normalized before the Preview is built",
        default=False,
    )
    sanitize_advanced_expanded: BoolProperty(
        name="Advanced",
        description="Less common Cleanup options (length limits, numbers, synonyms, blacklist)",
        default=False,
    )
    sanitize_separators: EnumProperty(
        name="Separators",
        description="Characters used to split multi-Tag input",
        items=[
            ("COMMA", "Comma", "Split on ','"),
            ("SEMICOLON", "Semicolon", "Split on ';'"),
            ("COMMA_SEMICOLON", "Comma + Semicolon", "Split on ',' or ';'"),
            ("SPACE", "Space", "Split on spaces"),
            ("PIPE", "Pipe", "Split on '|'"),
            ("DOUBLE_HYPHEN", "Double Hyphen", "Split on '--' and single hyphens (e.g. cc-rosse--red)"),
        ],
        default="COMMA_SEMICOLON",
    )
    sanitize_casing: EnumProperty(
        name="Casing",
        description="Letter case policy applied to Tags",
        items=[
            ("TITLE", "Title Case", "Capitalize every word (e.g. Oak Tree)"),
            ("SNAKE", "Snake Case", "Lowercase words joined with underscores (e.g. oak_tree)"),
            ("CAMEL", "Camel Case", "First word lowercase, subsequent words capitalized (e.g. oakTree)"),
            ("PASCAL", "Pascal Case", "Every word capitalized with no separator (e.g. OakTree)"),
            ("KEBAB", "Kebab Case", "Lowercase words joined with hyphens (e.g. oak-tree)"),
            ("UPPER", "Upper Case", "All lowercase words rendered uppercase (e.g. OAK TREE)"),
            ("LOWER", "Lower Case", "All uppercase words rendered lowercase (e.g. oak tree)"),
            ("NONE", "None / Keep Source", "Leave the original casing untouched"),
        ],
        default="TITLE",
    )
    sanitize_sort: BoolProperty(
        name="Sort Alphabetically",
        description="Sort the final Tag list alphabetically (case-insensitive)",
        default=True,
    )
    sanitize_max_length: IntProperty(
        name="Max Tag Length",
        description="Maximum number of characters allowed per Tag. Blender's hard limit is 63",
        default=63,
        min=1,
        max=63,
    )
    sanitize_max_tags: IntProperty(
        name="Max Tags per Asset",
        description="Hard cap on total Tags per asset. Excess Tags from rules, knowledge and manual input are dropped deterministically. 0 disables the cap",
        default=0,
        min=0,
        max=200,
    )
    sanitize_remove_numbers: BoolProperty(
        name="Remove Trailing Numbers",
        description="Strip trailing digits from Tags (e.g. 'Rock_01' -> 'Rock')",
        default=True,
    )
    sanitize_merge_synonyms: BoolProperty(
        name="Merge Synonyms",
        description="Merge Tags that are synonyms according to the synonym dictionary",
        default=False,
    )
    sanitize_blacklist: StringProperty(
        name="Blacklist",
        description="Comma-separated Tags that will be removed during sanitization",
        default="",
    )
    sanitize_segment: BoolProperty(
        name="Split Compound Words",
        description="Prudently decompose glued words only when every piece is a real word of 3+ letters (oaktree -> oak, tree). Off by default; never breaks names like 'annette'",
        default=False,
        update=_live_recompile,
    )
    sanitize_recombine: EnumProperty(
        name="Compound Result",
        description="How compound words appear after splitting (updates the Review live)",
        items=[
            ("BOTH", "Both", "Keep the split words and the recombined unit"),
            ("SPLIT", "Split", "Keep only the individual split words"),
            ("RECOMBINED", "Combined", "Keep only the recombined unit"),
        ],
        default="BOTH",
        update=_live_recompile,
    )
    review_step: EnumProperty(
        name="Review Step",
        items=[
            ("ADD", "Add", "Tags to add across the selection"),
            ("REMOVE", "Remove", "Tags to remove across the selection"),
            ("SANITIZE", "Cleanup", "Normalization and segmentation applied before applying"),
            ("ASSETS", "Last Step", "Final Tags preview and per-asset fine-tune"),
        ],
        default="ADD",
    )
    # Per-step activation. Each Review step (Add / Remove / Cleanup / Last Step)
    # can be turned on or off independently before applying; toggling updates the
    # Preview live through the shared guarded recompile path.
    review_add_on: BoolProperty(name="Add", description="Apply the Add step", default=True, update=_live_recompile)
    review_remove_on: BoolProperty(name="Remove", description="Apply the Remove step", default=True, update=_live_recompile)
    review_cleanup_on: BoolProperty(name="Cleanup", description="Apply the Cleanup step (normalization and segmentation)", default=True, update=_live_recompile)
    review_assets_on: BoolProperty(name="Last Step", description="Apply the per-asset fine-tune operations queued in the Last Step", default=True, update=_live_recompile)
    # --- Per-asset Tag list: pagination + filters (Last Step detail view) ---
    review_tag_page: IntProperty(name="Tag Page", description="Current page of the inspected asset's Tag list", default=0, min=0)
    review_tag_filter_mode: EnumProperty(
        name="Tag Filter",
        description="How to filter the Tags shown for the inspected asset",
        items=[
            ("ALL", "All", "Show every Tag"),
            ("LENGTH", "By Length", "Show only Tags whose letter count is within the range"),
            ("CATEGORY", "By Category", "Show only Tags matching a manual / category keyword"),
            ("COMPOUND", "Compound", "Show only compound (multi-word) or only single-word Tags"),
        ],
        default="ALL",
    )
    review_tag_min_len: IntProperty(name="Min Letters", description="Minimum number of letters", default=1, min=1, max=100)
    review_tag_max_len: IntProperty(name="Max Letters", description="Maximum number of letters", default=100, min=1, max=100)
    review_tag_category: StringProperty(name="Category", description="Only show Tags whose text or reason matches this keyword", default="")
    review_tag_compound_multi: BoolProperty(
        name="Multi-word only",
        description="On: show only compound (multi-word) Tags. Off: show only single-word Tags",
        default=True,
    )



def _draw_taxonomy_hits(layout, term, domain_filter):
    from ..adapters.taxonomy_store import active_taxonomy
    from ..engine.taxonomy import search_term
    hits = search_term(active_taxonomy(), term)
    domain = (domain_filter or "").strip().casefold()
    if domain:
        hits = [h for h in hits if domain in str(h.get("domain", "")).casefold()]
    if not hits:
        layout.label(text=f"No catalog entry for '{term}'", icon="INFO")
        return
    layout.separator()
    for hit in hits[:20]:
        layout.label(text=f"{hit.get('kind')}: {hit.get('tag')}  ({hit.get('domain') or 'Auxiliary'})", icon="DOT")


CLASSES = (BATMPreferences, BATMRuntimeProperties)
