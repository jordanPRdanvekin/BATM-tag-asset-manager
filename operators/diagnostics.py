"""Diagnostics export, inventory refresh and persistent-backup recovery."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import time

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from ..adapters.blender_assets import (
    batm_preferences,
    current_tags,
    find_local_id,
    refresh_asset_browser,
    set_local_tags,
)
from ..adapters.storage import atomic_json_write
from ..adapters.worker_ipc import prepare_request
from ..core.models import AssetKey
from ..core.session import SESSION
from .. import BATM_VERSION_STRING
from ..engine.backup import delete_backup, recoverable_backups, update_backup_states, verify_backup
from ..engine.fingerprint import fingerprint_file
from ..engine.inventory import INVENTORY
from ..engine.logging import capture_exception, export_text
from ..engine.scheduler import BatchScheduler, adaptive_worker_count


class BATM_OT_refresh_all(bpy.types.Operator):
    """Refresh the Asset Browser and the inventory in one action."""

    bl_idname = "batm.refresh_all"
    bl_label = "Refresh"
    bl_description = "Refresh the Asset Browser and rebuild the library inventory"
    bl_options = {"REGISTER"}

    def execute(self, context):
        refresh_asset_browser(context)
        INVENTORY.begin(force=True)
        context.window_manager.batm_runtime.status = "Refresh started"
        return {"FINISHED"}


class BATM_OT_export_diagnostics(bpy.types.Operator, ExportHelper):
    bl_idname = "batm.export_diagnostics"
    bl_label = "Export Diagnostics"
    bl_description = "Export the current session state, snapshots and operations to a JSON report for inspection"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "batm_version": BATM_VERSION_STRING,
            "blender_version": bpy.app.version_string,
            "phase": SESSION.phase,
            "run_id": SESSION.run_id,
            "status": context.window_manager.batm_runtime.status,
            "snapshots": [snapshot.to_dict() for snapshot in SESSION.snapshots.values()],
            "operations": [operation.to_dict() for operation in SESSION.operations],
            "desired": [state.to_dict() for state in SESSION.desired.values()],
            "events": SESSION.messages,
            "inventory": [
                {
                    "name": index.name,
                    "root": index.root,
                    "blend_files": index.blend_files,
                    "asset_count": index.asset_count,
                    "errors": list(index.errors),
                    "scanned_at": index.scanned_at,
                }
                for index in INVENTORY.libraries.values()
            ],
            "recoverable_backups": [str(path) for path in recoverable_backups()],
        }
        try:
            atomic_json_write(self.filepath, payload)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not export diagnostics: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BATM_OT_export_log_text(bpy.types.Operator, ExportHelper):
    bl_idname = "batm.export_log_text"
    bl_label = "Export Last Log as Text"
    bl_description = "Export the most recent run log as a plain-text file for external review"
    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        path = context.window_manager.batm_runtime.last_log_path
        return bool(path and Path(path).is_file())

    def execute(self, context):
        try:
            export_text(context.window_manager.batm_runtime.last_log_path, self.filepath)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not export the log: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BATM_OT_discard_backup(bpy.types.Operator):
    bl_idname = "batm.discard_backup"
    bl_label = "Discard Recovery Backup"
    bl_description = "Permanently delete the selected recovery backup. The original Tags can no longer be restored from it"
    filepath: StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context):
        delete_backup(self.filepath)
        return {"FINISHED"}


class BATM_OT_restore_backup(bpy.types.Operator):
    bl_idname = "batm.restore_backup"
    bl_label = "Restore Latest BATM Backup"
    bl_description = "Restore original Tags from the latest verified recovery manifest"

    filepath: StringProperty()
    _timer = None
    _current_assets: list[dict[str, Any]]
    _started = 0.0

    @classmethod
    def poll(cls, _context):
        return SESSION.phase == "IDLE" and bool(recoverable_backups())

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            path = Path(self.filepath) if self.filepath else recoverable_backups()[-1]
            payload = verify_backup(path)
            SESSION.run_id = f"restore-{uuid4()}"
            self._started = time.monotonic()
            SESSION.set_phase("RESTORING")
            SESSION.backup_path = str(path)
            props = context.window_manager.batm_runtime
            props.phase = SESSION.phase
            props.status = "Restoring persistent BATM backup..."
            props.progress = 0.0
            context.window_manager.progress_begin(0, 100)

            current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            self._current_assets = []
            for item in payload.get("assets", []):
                blend_path = str(Path(item["key"]["blend_path"]).resolve())
                if blend_path == current_path:
                    self._current_assets.append(item)
                else:
                    grouped[blend_path].append(item)
            prepared = []
            for index, (blend_path, items) in enumerate(sorted(grouped.items())):
                prepared.append(
                    prepare_request(
                        SESSION.run_id,
                        30000 + index,
                        {
                            "mode": "RESTORE",
                            "blend_path": blend_path,
                            "fingerprint": fingerprint_file(blend_path),
                            "assets": [
                                {
                                    "key": item["key"],
                                    "expected_tags": item["applied_tags"],
                                    "desired_tags": item["original_tags"],
                                }
                                for item in items
                            ],
                        },
                        timeout_seconds=float(
                            getattr(batm_preferences(context), "worker_timeout_seconds", 2000)
                        ),
                    )
                )
            if not prepared:
                return self._restore_current_and_finish(context)
            SESSION.scheduler = BatchScheduler(prepared, adaptive_worker_count(len(prepared), 4))
            SESSION.scheduler.start_available()
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            capture_exception(
                code="RESTORE_SETUP_FAILED",
                message=str(exc),
                operation="restore backup",
                phase=SESSION.phase,
                context="BATM_OT_restore_backup.execute",
                exc=exc,
            )
            if SESSION.phase == "RESTORING":
                SESSION.set_phase("FAILED")
                SESSION.set_phase("IDLE")
            context.window_manager.progress_end()
            context.window_manager.batm_runtime.status = f"Restore could not start: {exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        scheduler = SESSION.scheduler
        if scheduler is None:
            # The scheduler vanished (e.g. add-on disabled mid-restore): exit
            # cleanly and keep the backup for a later recovery.
            if self._timer:
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            context.window_manager.progress_end()
            if SESSION.phase == "RESTORING":
                SESSION.set_phase("FAILED")
                SESSION.set_phase("IDLE")
            props = context.window_manager.batm_runtime
            props.phase = "IDLE"
            props.progress = 0.0
            props.status = "Restore interrupted; backup retained"
            return {"CANCELLED"}
        scheduler.poll()
        props = context.window_manager.batm_runtime
        props.progress = scheduler.confirmed_assets / max(1, scheduler.total_assets)
        props.eta_reliable = True
        props.elapsed_seconds = max(0.0, time.monotonic() - self._started)
        props.eta_seconds = (
            props.elapsed_seconds * (1.0 - props.progress) / props.progress if props.progress > 0.0 else 0.0
        )
        props.progress_detail = (
            f"Assets verified: {scheduler.confirmed_assets}/{scheduler.total_assets}  "
            f"Files: {scheduler.confirmed}/{scheduler.total}  Failed: {len(scheduler.failed)}"
        )
        context.window_manager.progress_update(props.progress * 100)
        status = scheduler.latest_status()
        if status.get("current_asset"):
            props.status = f"Restoring: {status['current_asset']}"
        if not scheduler.done:
            return {"PASS_THROUGH"}
        if scheduler.failed:
            return self._fail(context, scheduler.failed[0]["result"].get("error", "Restore worker failed"))
        return self._restore_current_and_finish(context)

    def _restore_current_and_finish(self, context):
        try:
            if self._current_assets:
                if not bpy.data.is_saved or bpy.data.is_dirty:
                    raise RuntimeError("Current file must be saved and clean before recovery")
                for item in self._current_assets:
                    key = AssetKey.from_dict(item["key"])
                    datablock = find_local_id(key)
                    if datablock is None:
                        raise LookupError(f"Recovery asset not found: {key.token}")
                    current = current_tags(datablock)
                    original = list(item["original_tags"])
                    if current == original:
                        continue
                    if current != list(item["applied_tags"]):
                        raise RuntimeError(f"Recovery Tag conflict: {key.token}")
                    set_local_tags(datablock, original)
                result = bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
                if "FINISHED" not in result:
                    raise RuntimeError("Could not save recovered current file")
            if self._timer:
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            context.window_manager.progress_update(100)
            context.window_manager.progress_end()
            restored_payload = verify_backup(SESSION.backup_path)
            update_backup_states(
                SESSION.backup_path,
                {str(item["key"]["blend_path"]) for item in restored_payload.get("assets", [])},
                "RESTORED",
            )
            delete_backup(SESSION.backup_path)
            SESSION.set_phase("RESTORED")
            SESSION.set_phase("REFRESHING")
            refresh_asset_browser(context)
            SESSION.set_phase("IDLE")
            props = context.window_manager.batm_runtime
            props.phase = "IDLE"
            props.progress = 0.0
            props.status = "Backup restored and verified"
            return {"FINISHED"}
        except Exception as exc:
            capture_exception(
                code="RESTORE_FAILED",
                message=str(exc),
                operation="restore backup",
                phase=SESSION.phase,
                context="BATM_OT_restore_backup._restore_current_and_finish",
                exc=exc,
            )
            return self._fail(context, str(exc))

    def _fail(self, context, message: str):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        if SESSION.phase == "RESTORING":
            SESSION.set_phase("FAILED")
        if SESSION.phase in ("FAILED", "RESTORED"):
            SESSION.set_phase("IDLE")
        props = context.window_manager.batm_runtime
        props.phase = "IDLE"
        props.status = f"Restore failed; backup retained: {message}"
        props.diagnostics_expanded = True
        self.report({"ERROR"}, message)
        return {"CANCELLED"}


CLASSES = (
    BATM_OT_refresh_all,
    BATM_OT_export_diagnostics,
    BATM_OT_export_log_text,
    BATM_OT_discard_backup,
    BATM_OT_restore_backup,
)
