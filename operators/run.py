"""Analysis, editable Review, execution and automatic rollback operators."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import shutil
import time

import bpy
from bpy.props import IntProperty, StringProperty

from ..adapters.blender_assets import (
    apply_current_file,
    current_tags,
    find_local_id,
    set_local_tags,
    snapshot_selection,
)
from ..adapters.worker_ipc import prepare_request
from ..core.models import AssetKey, DesiredAssetState, TagOperation
from ..core.session import SESSION
from ..engine.autotag import build_autotag_operations
from ..engine.backup import create_backup, delete_backup, update_backup_states
from ..engine.fingerprint import fingerprint_file, fingerprints_match
from ..engine.logging import persist_run_log, prune_logs
from ..engine.scheduler import BatchScheduler, adaptive_worker_count


def _runtime(context):
    return context.window_manager.batm_runtime


def _preferences(context):
    for key, addon in context.preferences.addons.items():
        if key.endswith("batch_asset_tag_manager") or key.endswith("BATM_4.0.0"):
            return addon.preferences
    return None


def _sync_runtime(context, status: str | None = None) -> None:
    props = _runtime(context)
    props.phase = SESSION.phase
    if status is not None:
        props.status = status


def _update_timing(context, started: float, fraction: float, detail: str = "") -> None:
    props = _runtime(context)
    elapsed = max(0.0, time.monotonic() - started)
    props.elapsed_seconds = elapsed
    props.eta_seconds = elapsed * (1.0 - fraction) / fraction if fraction > 0.0 else 0.0
    props.progress_detail = detail


def _recompile_preserving_disabled() -> None:
    disabled = {token for token, state in SESSION.desired.items() if not state.enabled}
    SESSION.compile()
    for token in disabled:
        if token in SESSION.desired:
            SESSION.desired[token].enabled = False


def _refresh_asset_browser(context) -> bool:
    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "FILE_BROWSER" or getattr(area.spaces.active, "browse_mode", "") != "ASSETS":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            try:
                with context.temp_override(window=window, screen=screen, area=area, region=region):
                    result = bpy.ops.asset.library_refresh()
                    return "FINISHED" in result
            except RuntimeError:
                continue
    return False


def _finish_idle(context, status: str, clear_operations: bool = False) -> None:
    if SESSION.phase != "IDLE":
        SESSION.set_phase("IDLE")
    if clear_operations:
        SESSION.operations.clear()
        SESSION.selected_tags.clear()
    _sync_runtime(context, status)
    _runtime(context).progress = 0.0


def _worker_assets(snapshots) -> list[dict[str, Any]]:
    return [{"key": snapshot.key.to_dict()} for snapshot in snapshots]


def _completed_paths(scheduler: BatchScheduler | None) -> set[str]:
    if scheduler is None:
        return set()
    return {
        str(record.get("result", {}).get("blend_path", ""))
        for record in scheduler.completed
        if record.get("result", {}).get("blend_path")
    }


def _log_scheduler_results(scheduler: BatchScheduler, action: str) -> None:
    code = "FILE_APPLIED" if action == "APPLY" else "FILE_RESTORED"
    asset_code = "ASSET_APPLIED" if action == "APPLY" else "ASSET_RESTORED"
    for record in scheduler.completed:
        result = record.get("result", {})
        blend_path = result.get("blend_path", "")
        SESSION.add_message("INFO", code, f"{action.title()} verified: {blend_path}", file=blend_path)
        for asset in result.get("assets", []):
            SESSION.add_message(
                "INFO",
                asset_code,
                f"{action.title()} verified for {asset.get('key', {}).get('datablock_name', '')}",
                file=blend_path,
                asset=asset.get("key", {}),
            )


def _validate_execution_preflight(states: list[DesiredAssetState]) -> None:
    current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
    if any(state.key.blend_path == current_path for state in states):
        if not bpy.data.is_saved or bpy.data.is_dirty:
            raise RuntimeError("Current file must be saved with no pending changes")
    checked: set[str] = set()
    for state in states:
        path = Path(state.key.blend_path)
        if str(path) in checked:
            continue
        checked.add(str(path))
        if not path.is_file():
            raise RuntimeError(f"Asset file is missing: {path}")
        required = max(64 * 1024 * 1024, int(path.stat().st_size * 1.1))
        available = shutil.disk_usage(path.parent).free
        if available < required:
            raise RuntimeError(f"Insufficient free disk space to safely save: {path}")


class BATM_OT_run(bpy.types.Operator):
    bl_idname = "batm.run"
    bl_label = "Run BATM"
    bl_description = "Freeze the selection, analyze AutoTag rules and open editable Review"
    bl_options = {"REGISTER"}

    _timer = None
    _started = 0.0

    @classmethod
    def poll(cls, context):
        return SESSION.phase == "IDLE" and bool(getattr(context, "selected_assets", None))

    def execute(self, context):
        try:
            asset_type_filter = _runtime(context).asset_type_filter
            snapshots = snapshot_selection(context, asset_type_filter)
            if not snapshots:
                self.report({"WARNING"}, "Select at least one Asset Browser asset")
                return {"CANCELLED"}
            tokens = [snapshot.key.token for snapshot in snapshots]
            if len(tokens) != len(set(tokens)):
                raise RuntimeError("Selection contains ambiguous duplicate Asset identities")
            SESSION.begin()
            self._started = time.monotonic()
            SESSION.snapshots = {snapshot.key.token: snapshot for snapshot in snapshots}
            SESSION.add_message("INFO", "ANALYSIS_STARTED", f"Analyzing {len(snapshots)} selected assets")
            _sync_runtime(context, "Analyzing selection...")
            _runtime(context).progress = 0.0
            context.window_manager.progress_begin(0, 100)

            current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
            grouped: dict[str, list] = defaultdict(list)
            for snapshot in snapshots:
                if snapshot.writable and snapshot.key.blend_path and snapshot.key.blend_path != current_path:
                    grouped[snapshot.key.blend_path].append(snapshot)
                elif snapshot.excluded_reason:
                    SESSION.add_message(
                        "WARNING", "ASSET_EXCLUDED", snapshot.excluded_reason, asset=snapshot.key.to_dict()
                    )

            prepared = []
            for index, (path, items) in enumerate(sorted(grouped.items())):
                prepared.append(
                    prepare_request(
                        SESSION.run_id,
                        index,
                        {
                            "mode": "ANALYZE",
                            "blend_path": path,
                            "fingerprint": items[0].fingerprint,
                            "assets": _worker_assets(items),
                        },
                    )
                )
            if not prepared:
                self._finish_analysis(context)
                return {"FINISHED"}

            prefs = _preferences(context)
            limit = int(getattr(prefs, "max_workers", 4))
            scheduler = BatchScheduler(prepared, adaptive_worker_count(len(prepared), limit))
            SESSION.scheduler = scheduler
            scheduler.start_available()
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            if SESSION.phase == "ANALYZING":
                SESSION.set_phase("FAILED")
                SESSION.add_message("ERROR", "ANALYSIS_SETUP_FAILED", str(exc))
                SESSION.set_phase("IDLE")
            context.window_manager.progress_end()
            _sync_runtime(context, f"Analysis failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        scheduler = SESSION.scheduler
        if event.type == "ESC" and scheduler:
            scheduler.cancel()
            _runtime(context).status = "Cancelling analysis..."
        if event.type != "TIMER" or scheduler is None:
            return {"PASS_THROUGH"}
        scheduler.poll()
        total = max(1, scheduler.total)
        _runtime(context).progress = scheduler.confirmed_assets / max(1, scheduler.total_assets)
        _update_timing(
            context,
            self._started,
            _runtime(context).progress,
            (
                f"Assets verified: {scheduler.confirmed_assets}/{scheduler.total_assets}  "
                f"Files: {scheduler.confirmed}/{total}  Failed: {len(scheduler.failed)}"
            ),
        )
        context.window_manager.progress_update(_runtime(context).progress * 100)
        status = scheduler.latest_status()
        props = _runtime(context)
        if status.get("current_asset"):
            props.current_asset = str(status["current_asset"])
            props.status = f"Analyzing: {status['current_asset']}"
        if status.get("current_file"):
            props.current_file = str(status["current_file"])
        props.task_label = "Analyzing assets"
        if not scheduler.done:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        if scheduler.cancel_requested:
            SESSION.add_message("WARNING", "ANALYSIS_CANCELLED", "Analysis cancelled by user")
            context.window_manager.progress_end()
            _finish_idle(context, "Analysis cancelled")
            return {"CANCELLED"}
        if scheduler.failed:
            SESSION.set_phase("FAILED")
            for failure in scheduler.failed:
                message = failure["result"].get("error", "Background analysis failed")
                SESSION.add_message("ERROR", "ANALYSIS_WORKER_FAILED", message)
            SESSION.set_phase("IDLE")
            context.window_manager.progress_end()
            _sync_runtime(context, "Analysis failed; open Diagnostics")
            return {"CANCELLED"}
        for completed in scheduler.completed:
            result = completed["result"]
            for value in result.get("assets", []):
                key = AssetKey.from_dict(value["key"])
                snapshot = SESSION.snapshots.get(key.token)
                if snapshot:
                    snapshot.tags = [str(tag) for tag in value.get("tags", [])]
                    snapshot.facts.update(value.get("facts", {}))
        self._finish_analysis(context)
        return {"FINISHED"}

    def _finish_analysis(self, context) -> None:
        SESSION.operations = [operation for operation in SESSION.operations if operation.origin != "AUTO"]
        SESSION.operations.extend(build_autotag_operations(list(SESSION.snapshots.values()), SESSION.rules))
        SESSION.compile()
        SESSION.set_phase("REVIEW_READY")
        changed = sum(1 for state in SESSION.desired.values() if state.changed)
        errors = sum(len(state.warnings) for state in SESSION.desired.values())
        SESSION.add_message("INFO", "REVIEW_READY", f"Review contains {changed} changed assets")
        _runtime(context).progress = 1.0
        _update_timing(context, self._started, 1.0, f"Assets analyzed: {len(SESSION.snapshots)}")
        context.window_manager.progress_update(100)
        context.window_manager.progress_end()
        _runtime(context).review_page = 0
        _sync_runtime(context, f"Review ready: {changed} changed assets, {errors} warnings")


class BATM_OT_review_toggle_asset(bpy.types.Operator):
    bl_idname = "batm.review_toggle_asset"
    bl_label = "Enable / Disable Asset"
    token: StringProperty()

    def execute(self, _context):
        state = SESSION.desired.get(self.token)
        if state:
            state.enabled = not state.enabled
        return {"FINISHED"}


class BATM_OT_review_select(bpy.types.Operator):
    bl_idname = "batm.review_select"
    bl_label = "Inspect Asset"
    token: StringProperty()

    def execute(self, _context):
        SESSION.active_review_token = self.token
        return {"FINISHED"}


class BATM_OT_review_add_tag(bpy.types.Operator):
    bl_idname = "batm.review_add_tag"
    bl_label = "Add Review Tag"
    token: StringProperty()
    value: StringProperty(name="Tag")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not self.value.strip() or self.token not in SESSION.desired:
            return {"CANCELLED"}
        SESSION.operations.append(
            TagOperation(
                kind="ADD",
                targets=[self.token],
                values=[self.value],
                origin="PREVIEW",
                explanation="Review Add",
            )
        )
        _recompile_preserving_disabled()
        _sync_runtime(context, "Review updated")
        return {"FINISHED"}


class BATM_OT_review_remove_tag(bpy.types.Operator):
    bl_idname = "batm.review_remove_tag"
    bl_label = "Remove Review Tag"
    token: StringProperty()
    value: StringProperty()

    def execute(self, context):
        if self.token not in SESSION.desired:
            return {"CANCELLED"}
        SESSION.operations.append(
            TagOperation(
                kind="REMOVE",
                targets=[self.token],
                values=[self.value],
                origin="PREVIEW",
                explanation="Review Remove",
            )
        )
        _recompile_preserving_disabled()
        _sync_runtime(context, "Review updated")
        return {"FINISHED"}


class BATM_OT_review_toggle_operation(bpy.types.Operator):
    bl_idname = "batm.review_toggle_operation"
    bl_label = "Enable / Disable Operation"
    index: IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(SESSION.operations):
            SESSION.operations[self.index].enabled = not SESSION.operations[self.index].enabled
            _recompile_preserving_disabled()
            _sync_runtime(context, "Review operation updated")
        return {"FINISHED"}


class BATM_OT_review_page(bpy.types.Operator):
    bl_idname = "batm.review_page"
    bl_label = "Review Page"
    delta: IntProperty()

    def execute(self, context):
        props = _runtime(context)
        props.review_page = max(0, props.review_page + self.delta)
        return {"FINISHED"}


class BATM_OT_review_cancel(bpy.types.Operator):
    bl_idname = "batm.review_cancel"
    bl_label = "Cancel Review"

    def execute(self, context):
        SESSION.set_phase("IDLE")
        SESSION.snapshots.clear()
        SESSION.desired.clear()
        SESSION.operations.clear()
        _sync_runtime(context, "Review cancelled; no files were changed")
        _runtime(context).progress = 0.0
        return {"FINISHED"}


class BATM_OT_execute(bpy.types.Operator):
    bl_idname = "batm.execute"
    bl_label = "Confirm and Apply"
    bl_description = "Create a verified backup and apply only the enabled Review changes"
    bl_options = {"REGISTER"}

    _timer = None
    _mode = "APPLY"
    _current_states: list[DesiredAssetState]
    _current_touched = False
    _started = 0.0

    @classmethod
    def poll(cls, _context):
        enabled = [state for state in SESSION.desired.values() if state.changed]
        return SESSION.phase == "REVIEW_READY" and bool(enabled) and all(state.valid for state in enabled)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            self._mode = "APPLY"
            self._current_touched = False
            self._started = time.monotonic()
            states = [state for state in SESSION.desired.values() if state.changed]
            if not states or any(not state.valid for state in states):
                raise RuntimeError("Review contains no valid enabled changes")
            _validate_execution_preflight(states)
            SESSION.set_phase("APPROVED")
            SESSION.set_phase("BACKING_UP")
            backup = create_backup(SESSION.run_id, list(SESSION.snapshots.values()), SESSION.desired)
            SESSION.backup_path = str(backup)
            SESSION.set_phase("BACKED_UP")
            SESSION.add_message("INFO", "BACKUP_READY", f"Verified backup created: {backup}")

            current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
            grouped: dict[str, list[DesiredAssetState]] = defaultdict(list)
            self._current_states = []
            for state in states:
                if state.key.blend_path == current_path:
                    self._current_states.append(state)
                else:
                    grouped[state.key.blend_path].append(state)
            prepared = []
            for index, (path, items) in enumerate(sorted(grouped.items())):
                snapshot = SESSION.snapshots[items[0].key.token]
                prepared.append(
                    prepare_request(
                        SESSION.run_id,
                        10000 + index,
                        {
                            "mode": "APPLY",
                            "blend_path": path,
                            "fingerprint": snapshot.fingerprint,
                            "assets": [
                                {
                                    "key": state.key.to_dict(),
                                    "expected_tags": state.before,
                                    "desired_tags": state.after,
                                }
                                for state in items
                            ],
                        },
                    )
                )
            SESSION.execution_requests = prepared
            SESSION.set_phase("EXECUTING")
            _sync_runtime(context, "Applying approved changes...")
            _runtime(context).progress = 0.0
            context.window_manager.progress_begin(0, 100)
            if not prepared:
                return self._apply_current_and_finish(context)
            prefs = _preferences(context)
            limit = int(getattr(prefs, "max_workers", 4))
            SESSION.scheduler = BatchScheduler(prepared, adaptive_worker_count(len(prepared), limit))
            SESSION.scheduler.start_available()
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            SESSION.add_message("ERROR", "EXECUTION_SETUP_FAILED", str(exc))
            if SESSION.scheduler is not None:
                SESSION.scheduler.cancel()
            if SESSION.phase in {"APPROVED", "BACKING_UP", "BACKED_UP", "EXECUTING"}:
                SESSION.set_phase("FAILED")
                SESSION.set_phase("IDLE")
            _sync_runtime(context, f"Execution failed: {exc}")
            context.window_manager.progress_end()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        scheduler = SESSION.scheduler
        if event.type == "ESC" and self._mode == "APPLY" and scheduler:
            scheduler.cancel()
            _runtime(context).status = "Cancellation requested; waiting for safe rollback..."
        if event.type != "TIMER" or scheduler is None:
            return {"PASS_THROUGH"}
        scheduler.poll()
        _runtime(context).progress = scheduler.confirmed_assets / max(1, scheduler.total_assets)
        _update_timing(
            context,
            self._started,
            _runtime(context).progress,
            (
                f"Assets verified: {scheduler.confirmed_assets}/{scheduler.total_assets}  "
                f"Files: {scheduler.confirmed}/{scheduler.total}  Failed: {len(scheduler.failed)}"
            ),
        )
        context.window_manager.progress_update(_runtime(context).progress * 100)
        status = scheduler.latest_status()
        props = _runtime(context)
        if status.get("current_asset"):
            props.current_asset = str(status["current_asset"])
            props.status = f"{self._mode.title()}: {status['current_asset']}"
        if status.get("current_file"):
            props.current_file = str(status["current_file"])
        props.task_label = "Applying approved changes" if self._mode == "APPLY" else "Restoring original Tags"
        if not scheduler.done:
            return {"PASS_THROUGH"}

        if self._mode == "RESTORE":
            if scheduler.failed:
                for failure in scheduler.failed:
                    SESSION.add_message(
                        "ERROR", "RESTORE_WORKER_FAILED", failure["result"].get("error", "Restore failed")
                    )
                return self._finish_restore_failed(context)
            _log_scheduler_results(scheduler, "RESTORE")
            return self._finish_restored(context)

        if scheduler.failed or scheduler.cancel_requested:
            for failure in scheduler.failed:
                SESSION.add_message(
                    "ERROR", "APPLY_WORKER_FAILED", failure["result"].get("error", "Apply failed")
                )
            return self._begin_rollback(context, cancelled=scheduler.cancel_requested)
        _log_scheduler_results(scheduler, "APPLY")
        completed_paths = _completed_paths(scheduler)
        if completed_paths:
            update_backup_states(SESSION.backup_path, completed_paths, "APPLIED")
        return self._apply_current_and_finish(context)

    def _apply_current_and_finish(self, context):
        try:
            if self._current_states:
                expected = SESSION.snapshots[self._current_states[0].key.token].fingerprint
                actual = fingerprint_file(bpy.data.filepath)
                if not fingerprints_match(expected, actual):
                    raise RuntimeError("Current file changed after Review")
                self._current_touched = True
                apply_current_file(self._current_states)
                update_backup_states(
                    SESSION.backup_path,
                    {state.key.blend_path for state in self._current_states},
                    "APPLIED",
                )
                for state in self._current_states:
                    SESSION.add_message(
                        "INFO",
                        "ASSET_APPLIED",
                        f"Apply verified for {state.key.datablock_name}",
                        file=state.key.blend_path,
                        asset=state.key.to_dict(),
                    )
            return self._finish_success(context)
        except Exception as exc:
            SESSION.add_message("ERROR", "CURRENT_FILE_FAILED", str(exc))
            return self._begin_rollback(context, cancelled=False)

    def _begin_rollback(self, context, cancelled: bool):
        scheduler = SESSION.scheduler
        completed = list(scheduler.completed) if scheduler else []
        completed_paths = _completed_paths(scheduler)
        if completed_paths:
            update_backup_states(SESSION.backup_path, completed_paths, "APPLIED")
        if SESSION.phase == "EXECUTING":
            SESSION.set_phase("CANCEL_REQUESTED" if cancelled else "PARTIAL_FAILED")
        SESSION.set_phase("RESTORING")
        SESSION.add_message(
            "WARNING", "ROLLBACK_STARTED", "Restoring every file already written", cancelled=cancelled
        )
        restore_jobs = []
        rollback_run_id = f"{SESSION.run_id}-rollback"
        for index, completed_job in enumerate(completed):
            request = completed_job["prepared"]["request"]
            result = completed_job["result"]
            restore_jobs.append(
                prepare_request(
                    rollback_run_id,
                    20000 + index,
                    {
                        "mode": "RESTORE",
                        "blend_path": request["blend_path"],
                        "fingerprint": result.get("fingerprint_after", {}),
                        "assets": [
                            {
                                "key": item["key"],
                                "expected_tags": item["desired_tags"],
                                "desired_tags": item["expected_tags"],
                            }
                            for item in request.get("assets", [])
                        ],
                    },
                )
            )
        try:
            if self._current_touched:
                for state in self._current_states:
                    datablock = find_local_id(state.key)
                    if datablock is None:
                        raise LookupError(f"Cannot restore current-file asset: {state.key.token}")
                    set_local_tags(datablock, state.before)
                result = bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
                if "FINISHED" not in result:
                    raise RuntimeError("Could not save current-file rollback")
                for state in self._current_states:
                    datablock = find_local_id(state.key)
                    if datablock is None or current_tags(datablock) != state.before:
                        raise RuntimeError(f"Current-file rollback verification failed: {state.key.token}")
                    SESSION.add_message(
                        "INFO",
                        "ASSET_RESTORED",
                        f"Restore verified for {state.key.datablock_name}",
                        file=state.key.blend_path,
                        asset=state.key.to_dict(),
                    )
        except Exception as exc:
            SESSION.add_message("ERROR", "CURRENT_ROLLBACK_FAILED", str(exc))
            return self._finish_restore_failed(context)
        if not restore_jobs:
            return self._finish_restored(context)
        self._mode = "RESTORE"
        prefs = _preferences(context)
        limit = int(getattr(prefs, "max_workers", 4))
        SESSION.scheduler = BatchScheduler(restore_jobs, adaptive_worker_count(len(restore_jobs), limit))
        SESSION.scheduler.start_available()
        _sync_runtime(context, "Rolling back completed files...")
        return {"RUNNING_MODAL"}

    def _remove_timer(self, context) -> None:
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _persist_log(self, context, outcome: str) -> None:
        path = persist_run_log(
            SESSION.run_id,
            SESSION.messages,
            {
                "outcome": outcome,
                "selected_assets": len(SESSION.snapshots),
                "changed_assets": sum(1 for state in SESSION.desired.values() if state.changed),
            },
        )
        _runtime(context).last_log_path = str(path)
        prefs = _preferences(context)
        prune_logs(
            int(getattr(prefs, "log_retention_days", 30)),
            int(getattr(prefs, "log_retention_runs", 50)),
        )

    def _finish_success(self, context):
        self._remove_timer(context)
        context.window_manager.progress_update(100)
        context.window_manager.progress_end()
        SESSION.set_phase("SUCCEEDED")
        SESSION.add_message("INFO", "EXECUTION_SUCCEEDED", "All approved Tags were saved and verified")
        delete_backup(SESSION.backup_path)
        self._persist_log(context, "SUCCEEDED")
        SESSION.set_phase("REFRESHING")
        refreshed = _refresh_asset_browser(context)
        _finish_idle(context, "Completed and verified" + ("" if refreshed else "; refresh unavailable"), True)
        return {"FINISHED"}

    def _finish_restored(self, context):
        self._remove_timer(context)
        context.window_manager.progress_update(100)
        context.window_manager.progress_end()
        SESSION.set_phase("RESTORED")
        SESSION.add_message("INFO", "ROLLBACK_SUCCEEDED", "Original Tags were restored and verified")
        update_backup_states(
            SESSION.backup_path,
            {snapshot.key.blend_path for snapshot in SESSION.snapshots.values()},
            "RESTORED",
        )
        delete_backup(SESSION.backup_path)
        self._persist_log(context, "RESTORED")
        SESSION.set_phase("REFRESHING")
        _refresh_asset_browser(context)
        _finish_idle(context, "Operation failed or was cancelled; rollback completed", True)
        return {"CANCELLED"}

    def _finish_restore_failed(self, context):
        self._remove_timer(context)
        context.window_manager.progress_end()
        SESSION.set_phase("FAILED")
        SESSION.add_message(
            "ERROR", "ROLLBACK_FAILED", f"Rollback failed; recovery backup retained at {SESSION.backup_path}"
        )
        self._persist_log(context, "ROLLBACK_FAILED")
        SESSION.set_phase("REFRESHING")
        _refresh_asset_browser(context)
        _finish_idle(context, "Rollback failed; open Diagnostics immediately")
        _runtime(context).diagnostics_expanded = True
        return {"CANCELLED"}


class BATM_OT_cancel_execution(bpy.types.Operator):
    bl_idname = "batm.cancel_execution"
    bl_label = "Cancel and Roll Back"

    @classmethod
    def poll(cls, _context):
        return SESSION.phase == "EXECUTING" and SESSION.scheduler is not None

    def execute(self, context):
        SESSION.scheduler.cancel()
        _runtime(context).status = "Cancellation requested; running saves will finish safely"
        return {"FINISHED"}


CLASSES = (
    BATM_OT_run,
    BATM_OT_review_toggle_asset,
    BATM_OT_review_select,
    BATM_OT_review_add_tag,
    BATM_OT_review_remove_tag,
    BATM_OT_review_toggle_operation,
    BATM_OT_review_page,
    BATM_OT_review_cancel,
    BATM_OT_execute,
    BATM_OT_cancel_execution,
)
