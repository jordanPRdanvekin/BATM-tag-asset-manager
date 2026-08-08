"""Blender RNA properties used only for UI and persistent preferences."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

ADDON_ID = __package__.rsplit(".", 1)[0]


class BATMPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    max_workers: IntProperty(
        name="Maximum Workers",
        description="Upper bound for adaptive Blender background processes",
        default=4,
        min=1,
        max=4,
    )
    review_page_size: IntProperty(name="Review Page Size", default=20, min=5, max=100)
    log_retention_days: IntProperty(name="Log Retention (Days)", default=30, min=1, max=3650)
    log_retention_runs: IntProperty(name="Maximum Run Logs", default=50, min=1, max=1000)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "max_workers")
        layout.prop(self, "review_page_size")
        layout.separator()
        layout.prop(self, "log_retention_days")
        layout.prop(self, "log_retention_runs")


class BATMRuntimeProperties(bpy.types.PropertyGroup):
    phase: StringProperty(name="Phase", default="IDLE")
    status: StringProperty(name="Status", default="Ready")
    progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    elapsed_seconds: FloatProperty(name="Elapsed", default=0.0, min=0.0)
    eta_seconds: FloatProperty(name="ETA", default=0.0, min=0.0)
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
        description="Filter the Tag index by name. Selecting a Tag also selects the assets that own it",
    )
    review_search: StringProperty(
        name="Search Review",
        description="Filter the Review list by asset name, ID type, blend path or proposed Tag",
    )
    review_page: IntProperty(name="Page", default=0, min=0)
    rule_search: StringProperty(
        name="Search Rules",
        description="Filter AutoTag rules by name",
    )
    rule_active_index: IntProperty(name="Active Rule", default=-1)
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

    # Collapsible top-level sections.
    metrics_expanded: BoolProperty(
        name="Current Library",
        description="Show the active library, asset count, blend files, selection, catalog and filter",
        default=True,
    )
    run_expanded: BoolProperty(
        name="Run BATM",
        description="Start the guided AutoTag → Review → Apply pipeline",
        default=True,
    )
    settings_expanded: BoolProperty(
        name="Settings",
        description="Configure AutoTag rules and Sanitizer behaviour",
        default=False,
    )

    # Manual Tag Editor expanded tabs (one per collapsible section).
    manual_tab_edit: BoolProperty(
        name="Add / Remove / Replace",
        description="Queue ADD, REMOVE and REPLACE operations for the selected assets",
        default=True,
    )
    manual_tab_actions: BoolProperty(
        name="Actions",
        description="Clear the pending queue or clone Tags from one asset to the rest of the selection",
        default=False,
    )
    manual_tab_search: BoolProperty(
        name="Search / Replace",
        description="Search the Tag index, select assets by Tag, and replace Tags across the selection",
        default=False,
    )
    manual_tab_tags: BoolProperty(
        name="Tags",
        description="Browse common (N/N) and partial/individual Tags with coverage counts",
        default=False,
    )
    # Individual asset tags page offset (pagination instead of arbitrary truncation).
    manual_tag_page: IntProperty(name="Tag Page", default=0, min=0)
    manual_page_size: IntProperty(
        name="Tags Per Page",
        description="Number of Tags shown per page in the Tag index",
        default=50,
        min=10,
        max=500,
    )
    manual_individual_token: StringProperty(name="Individual Asset")
    manual_individual_search: StringProperty(name="Search Individual")
    clone_source: StringProperty(
        name="Clone From",
        description="Asset whose Tags will be cloned to the rest of the selection. Leave empty to use the first selected asset",
    )
    replace_only_selected: BoolProperty(
        name="Replace in Selected Only",
        description="When enabled, Replace only affects the currently selected assets",
        default=True,
    )

    # Sanitize Rules configuration.
    sanitize_expanded: BoolProperty(
        name="Sanitize Rules",
        description="Configure how Tags are normalized before the Preview is built",
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
        ],
        default="COMMA_SEMICOLON",
    )
    sanitize_casing: EnumProperty(
        name="Casing",
        description="Casing policy applied to Tags",
        items=[
            ("TITLE", "Title Case", "Each word capitalized (e.g. Oak Tree)"),
            ("SNAKE", "snake_case", "Lowercase with underscores (e.g. oak_tree)"),
            ("CAMEL", "camelCase", "First word lower, rest capitalized (e.g. oakTree)"),
            ("PASCAL", "PascalCase", "Each word capitalized, no spaces (e.g. OakTree)"),
            ("KEBAB", "kebab-case", "Lowercase with hyphens (e.g. oak-tree)"),
            ("UPPER", "UPPERCASE", "All uppercase (e.g. OAK TREE)"),
            ("LOWER", "lowercase", "All lowercase (e.g. oak tree)"),
            ("NONE", "None", "Keep original casing"),
        ],
        default="TITLE",
    )
    asset_type_filter: EnumProperty(
        name="Asset Type Filter",
        description="Filter which asset types BATM processes. Respects the Asset Browser filter when possible",
        items=[
            ("ALL", "All", "Process all asset types"),
            ("OBJECT", "Objects", "Only Object assets"),
            ("COLLECTION", "Collections", "Only Collection assets"),
            ("MATERIAL", "Materials", "Only Material assets"),
            ("WORLD", "Worlds", "Only World assets"),
            ("ACTION", "Actions", "Only Action assets"),
            ("NODETREE", "Node Groups", "Only NodeTree assets"),
            ("GREASEPENCIL", "Grease Pencil", "Only Grease Pencil assets"),
        ],
        default="ALL",
    )
    sanitize_sort: BoolProperty(
        name="Sort Alphabetically",
        description="Sort the final Tag list alphabetically (case-insensitive)",
        default=True,
    )
    sanitize_max_length: IntProperty(
        name="Max Tag Length",
        description="Maximum number of characters allowed per Tag",
        default=63,
        min=1,
        max=255,
    )
    sanitize_remove_numbers: BoolProperty(
        name="Remove Trailing Numbers",
        description="Strip trailing digits from Tags (e.g. 'Rock_01' -> 'Rock')",
        default=False,
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


CLASSES = (BATMPreferences, BATMRuntimeProperties)
