"""Analysis, editable Review, execution and automatic rollback operators."""

from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
from typing import Any
import shutil
import time

import bpy
from bpy.props import IntProperty, StringProperty

from ..adapters.blender_assets import (
    apply_current_file,
    batm_preferences,
    current_tags,
    find_local_id,
    refresh_asset_browser,
    set_local_tags,
    snapshot_selection,
)
from ..adapters.storage import prune_run_files
from ..adapters.worker_ipc import prepare_request
from ..core.models import AssetKey, DesiredAssetState, TagOperation
from ..core.sanitizer import options_from_props
from ..core.session import SESSION
from ..engine.autotag import build_autotag_operations
from ..engine.knowledge import load_knowledge
from ..engine.backup import create_backup, delete_backup, prune_backups, update_backup_states
from ..engine.fingerprint import fingerprint_file, fingerprints_match
from ..engine.logging import persist_run_log, prune_logs
from ..engine.scheduler import BatchScheduler, adaptive_worker_count


def _runtime(context):
    return context.window_manager.batm_runtime


def _sync_runtime(context, status: str | None = None) -> None:
    props = _runtime(context)
    props.phase = SESSION.phase
    if status is not None:
        props.status = status


def _update_timing(context, started: float, fraction: float, detail: str = "") -> None:
    props = _runtime(context)
    elapsed = max(0.0, time.monotonic() - started)
    raw_eta = elapsed * (1.0 - fraction) / fraction if fraction > 0.0 else 0.0
    # EMA smoothing against the previously displayed ETA to avoid jumps.
    if props.eta_seconds <= 0.0:
        props.eta_seconds = raw_eta
    else:
        props.eta_seconds = props.eta_seconds * 0.65 + raw_eta * 0.35
    props.elapsed_seconds = elapsed
    props.progress_detail = detail


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


def _worker_timeout(context) -> float:
    return float(getattr(batm_preferences(context), "worker_timeout_seconds", 2000))


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
    _hashing = False
    _hash_paths: list[str] = []
    _hash_total = 0

    @classmethod
    def poll(cls, context):
        return SESSION.phase == "IDLE" and bool(getattr(context, "selected_assets", None))

    def execute(self, context):
        try:
            asset_type_filter = _runtime(context).asset_type_filter
            snapshots = snapshot_selection(context, asset_type_filter, defer_fingerprints=True)
            if not snapshots:
                self.report({"WARNING"}, "Select at least one Asset Browser asset")
                return {"CANCELLED"}
            tokens = [snapshot.key.token for snapshot in snapshots]
            if len(tokens) != len(set(tokens)):
                raise RuntimeError("Selection contains ambiguous duplicate Asset identities")
            SESSION.begin()
            prune_run_files()
            for path in prune_backups(int(getattr(batm_preferences(context), "log_retention_days", 30))):
                SESSION.add_message("INFO", "BACKUP_PRUNED", f"Removed old backup: {Path(path).name}")
            self._started = time.monotonic()
            SESSION.snapshots = {snapshot.key.token: snapshot for snapshot in snapshots}
            SESSION.sanitize_options = options_from_props(_runtime(context))
            SESSION.add_message("INFO", "ANALYSIS_STARTED", f"Analyzing {len(snapshots)} selected assets")
            _sync_runtime(context, "Preparing files...")
            _runtime(context).progress = 0.0
            _runtime(context).eta_seconds = 0.0
            _runtime(context).eta_reliable = False
            context.window_manager.progress_begin(0, 100)
            # Deferred fingerprinting: hash each unique .blend once in the modal
            # loop so huge files do not freeze the UI on "Analyze".
            self._hash_paths = sorted(
                {
                    snapshot.key.blend_path
                    for snapshot in snapshots
                    if snapshot.writable and snapshot.key.blend_path and not snapshot.fingerprint
                }
            )
            self._hash_total = len(self._hash_paths)
            self._hashing = bool(self._hash_paths)
            if not self._hashing and not self._start_analysis(context):
                context.window_manager.progress_end()
                return {"FINISHED"}
            self._timer = context.window_manager.event_timer_add(
                0.05 if self._hashing else 0.25, window=context.window
            )
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

    def _start_analysis(self, context) -> bool:
        """Group snapshots by .blend and dispatch ANALYZE workers. False when idle."""
        current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
        grouped: dict[str, list] = defaultdict(list)
        for snapshot in SESSION.snapshots.values():
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
                    timeout_seconds=_worker_timeout(context),
                )
            )
        if not prepared:
            self._finish_analysis(context)
            return False
        prefs = batm_preferences(context)
        limit = int(getattr(prefs, "max_workers", 4))
        SESSION.scheduler = BatchScheduler(prepared, adaptive_worker_count(len(prepared), limit))
        SESSION.scheduler.start_available()
        _sync_runtime(context, "Analyzing assets...")
        return True

    def _hash_modal(self, context):
        if self._hash_paths:
            path = self._hash_paths.pop()
            try:
                digest = fingerprint_file(path)
            except OSError:
                digest = {}
            for snapshot in SESSION.snapshots.values():
                if snapshot.key.blend_path == path:
                    snapshot.fingerprint = dict(digest)
        done = self._hash_total - len(self._hash_paths)
        fraction = done / max(1, self._hash_total)
        props = _runtime(context)
        props.progress = fraction * 0.40
        props.current_file = ""
        props.task_label = "Preparing files"
        props.eta_reliable = False
        _update_timing(context, self._started, fraction * 0.40, f"Files hashed: {done}/{self._hash_total}")
        context.window_manager.progress_update(props.progress * 100)
        if self._hash_paths:
            props.status = f"Preparing files ({done}/{self._hash_total})..."
            return {"PASS_THROUGH"}
        self._hashing = False
        if not self._start_analysis(context):
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
            context.window_manager.progress_update(100)
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    def modal(self, context, event):
        if event.type == "ESC" and self._hashing:
            self._hash_paths = []
            self._hashing = False
            SESSION.add_message("WARNING", "ANALYSIS_CANCELLED", "Analysis cancelled by user")
            context.window_manager.progress_end()
            _finish_idle(context, "Analysis cancelled")
            return {"CANCELLED"}
        if event.type == "TIMER" and self._hashing:
            return self._hash_modal(context)
        scheduler = SESSION.scheduler
        if event.type == "ESC" and scheduler:
            scheduler.cancel()
            _runtime(context).status = "Cancelling analysis..."
        if event.type != "TIMER" or scheduler is None:
            return {"PASS_THROUGH"}
        scheduler.poll()
        total = max(1, scheduler.total)
        asset_fraction = scheduler.confirmed_assets / max(1, scheduler.total_assets)
        _runtime(context).progress = 0.40 + asset_fraction * 0.55
        _runtime(context).eta_reliable = True
        _update_timing(
            context,
            self._started,
            0.40 + asset_fraction * 0.55,
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
                    snapshot.facts["library_reference"] = snapshot.key.library_reference
        self._finish_analysis(context)
        return {"FINISHED"}

    def _finish_analysis(self, context) -> None:
        _sync_runtime(context, "Building Preview...")
        SESSION.operations = [operation for operation in SESSION.operations if operation.origin != "AUTO"]
        SESSION.operations.extend(
            build_autotag_operations(list(SESSION.snapshots.values()), SESSION.rules, load_knowledge())
        )
        SESSION.sanitize_options = options_from_props(_runtime(context))
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


class BATM_OT_review_enable_all(bpy.types.Operator):
    bl_idname = "batm.review_enable_all"
    bl_label = "Enable All Assets"
    bl_description = "Enable every asset in the Review in one click"

    def execute(self, _context):
        for state in SESSION.desired.values():
            state.enabled = True
        _sync_runtime(_context, f"Enabled all {len(SESSION.desired)} assets")
        return {"FINISHED"}


class BATM_OT_review_disable_all(bpy.types.Operator):
    bl_idname = "batm.review_disable_all"
    bl_label = "Disable All Assets"
    bl_description = "Disable every asset in the Review in one click"

    def execute(self, _context):
        for state in SESSION.desired.values():
            state.enabled = False
        _sync_runtime(_context, "Disabled all assets")
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
        SESSION.recompile_preserving_disabled()
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
        SESSION.recompile_preserving_disabled()
        _sync_runtime(context, "Review updated")
        return {"FINISHED"}


class BATM_OT_review_toggle_operation(bpy.types.Operator):
    bl_idname = "batm.review_toggle_operation"
    bl_label = "Enable / Disable Operation"
    index: IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(SESSION.operations):
            SESSION.operations[self.index].enabled = not SESSION.operations[self.index].enabled
            SESSION.recompile_preserving_disabled()
            _sync_runtime(context, "Review operation updated")
        return {"FINISHED"}


class BATM_OT_review_page(bpy.types.Operator):
    bl_idname = "batm.review_page"
    bl_label = "Review Page"
    delta: IntProperty()

    def execute(self, context):
        props = _runtime(context)
        from ..core.session import filtered_review_states

        states = filtered_review_states(props.review_search, props.review_filter)
        prefs = batm_preferences(context)
        page_size = max(1, int(getattr(prefs, "review_page_size", 20)))
        page_count = max(1, math.ceil(len(states) / page_size))
        proposed = props.review_page + self.delta
        props.review_page = max(0, min(proposed, page_count - 1))
        return {"FINISHED"}


class BATM_OT_review_cancel(bpy.types.Operator):
    bl_idname = "batm.review_cancel"
    bl_label = "Discard Review"
    bl_description = "Discard the current Review and close the session. No file is changed"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        SESSION.set_phase("IDLE")
        SESSION.snapshots.clear()
        SESSION.desired.clear()
        SESSION.operations.clear()
        SESSION.selected_tags.clear()
        _sync_runtime(context, "Review discarded; no files were changed")
        _runtime(context).progress = 0.0
        _runtime(context).eta_reliable = False
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
                        timeout_seconds=_worker_timeout(context),
                    )
                )
            SESSION.set_phase("EXECUTING")
            _sync_runtime(context, "Applying approved changes...")
            _runtime(context).progress = 0.0
            _runtime(context).eta_seconds = 0.0
            _runtime(context).eta_reliable = False
            context.window_manager.progress_begin(0, 100)
            if not prepared:
                return self._apply_current_and_finish(context)
            prefs = batm_preferences(context)
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
        _runtime(context).eta_reliable = True
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
                    timeout_seconds=_worker_timeout(context),
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
        prefs = batm_preferences(context)
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
        prefs = batm_preferences(context)
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
        _runtime(context).last_summary = (
            f"Completed: {len(SESSION.snapshots)} assets, "
            f"{sum(1 for state in SESSION.desired.values() if state.changed)} changed — verified"
        )
        SESSION.set_phase("REFRESHING")
        refreshed = refresh_asset_browser(context)
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
        _runtime(context).last_summary = "Rollback completed: original Tags restored and verified"
        SESSION.set_phase("REFRESHING")
        refresh_asset_browser(context)
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
        refresh_asset_browser(context)
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


class BATM_OT_review_apply_valid(bpy.types.Operator):
    bl_idname = "batm.review_apply_valid"
    bl_label = "Apply Only Valid"
    bl_description = (
        "Disable every asset with warnings (the invalid ones) and confirm the remaining "
        "valid changes in one click"
    )

    def execute(self, context):
        for state in SESSION.desired.values():
            if state.enabled and state.warnings:
                state.enabled = False
        return bpy.ops.batm.execute()


CLASSES = (
    BATM_OT_run,
    BATM_OT_review_toggle_asset,
    BATM_OT_review_enable_all,
    BATM_OT_review_disable_all,
    BATM_OT_review_select,
    BATM_OT_review_add_tag,
    BATM_OT_review_remove_tag,
    BATM_OT_review_toggle_operation,
    BATM_OT_review_page,
    BATM_OT_review_cancel,
    BATM_OT_review_apply_valid,
    BATM_OT_execute,
    BATM_OT_cancel_execution,
)
