"""Batch Asset Tag Manager for Blender 5.2."""

from __future__ import annotations

import bpy

# Single source of truth for the add-on version. The manifest
# (blender_manifest.toml) is the packaging source and is synced manually.
BATM_VERSION = (3, 0, 0)
BATM_VERSION_STRING = ".".join(str(part) for part in BATM_VERSION)

from .adapters.rule_store import load_active_rules
from .adapters.storage import ensure_dirs
from .core.session import SESSION
from .engine.backup import recoverable_backups
from .engine.inventory import INVENTORY, inventory_timer
from .operators import CLASSES as OPERATOR_CLASSES
from .ui.panels import CLASSES as PANEL_CLASSES
from .ui.properties import BATMRuntimeProperties, CLASSES as PROPERTY_CLASSES

CLASSES = PROPERTY_CLASSES + OPERATOR_CLASSES + PANEL_CLASSES


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.batm_runtime = bpy.props.PointerProperty(type=BATMRuntimeProperties)
    ensure_dirs()
    SESSION.reset()
    SESSION.rules = load_active_rules()
    backups = recoverable_backups()
    if backups:
        SESSION.add_message(
            "WARNING",
            "RECOVERY_AVAILABLE",
            f"{len(backups)} recoverable BATM backup(s) found",
        )
    if not bpy.app.timers.is_registered(inventory_timer):
        bpy.app.timers.register(inventory_timer, first_interval=1.0, persistent=True)


def unregister() -> None:
    INVENTORY.shutdown()
    if SESSION.scheduler is not None:
        try:
            SESSION.scheduler.cancel()
        except Exception:
            pass
        SESSION.scheduler = None
    if bpy.app.timers.is_registered(inventory_timer):
        bpy.app.timers.unregister(inventory_timer)
    if hasattr(bpy.types.WindowManager, "batm_runtime"):
        del bpy.types.WindowManager.batm_runtime
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
