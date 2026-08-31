"""Atomic storage paths for user rules, backups, logs and run IPC."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import bpy

# Extension id must never be hardcoded: it is the manifest ``id`` and equals the
# top-level package name. Resolve it dynamically (SSOT) so path lookups stay
# correct regardless of the installed package identity.
from .. import __package__ as base_package


def base_dir() -> Path:
    override = os.environ.get("BATM_BASE_DIR")
    if override:
        candidate = Path(override).expanduser()
        # Resolve without requiring the path to exist yet; fall back to raw path
        # if resolution fails (e.g. non-existent parent).
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        # BATM_BASE_DIR is test-controlled; reject obvious traversal payloads
        # like ``../../etc`` that would escape the intended sandbox. The check
        # is intentionally permissive for absolute temp dirs used in tests.
        if ".." in Path(override).parts:
            # ``Path.resolve()`` already collapses ``..``; still guard the raw
            # string so ``BATM_BASE_DIR=../../tmp`` cannot be injected via env.
            raise ValueError(f"BATM_BASE_DIR must not contain '..': {override!r}")
        return resolved
    path = bpy.utils.extension_path_user(base_package, path="", create=True)
    return Path(path)


def ensure_dirs() -> dict[str, Path]:
    paths = {
        "base": base_dir(),
        "backups": base_dir() / "backups",
        "logs": base_dir() / "logs",
        "runs": base_dir() / "runs",
        "rules": base_dir() / "rules",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def prune_run_files(retention_days: int = 7) -> int:
    """Remove run IPC scratch files older than retention_days. Returns the count removed."""
    run_dir = ensure_dirs()["runs"]
    cutoff = time.time() - max(1, retention_days) * 86400
    removed = 0
    for path in run_dir.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def atomic_json_write(path: str | Path, value: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(4):
        try:
            os.replace(temporary, target)
            return target
        except OSError:
            if os.name != "nt":
                temporary.unlink(missing_ok=True)
                raise
            # Windows: os.replace fails with WinError 5 (Access Denied) when a
            # concurrent reader has the target open without FILE_SHARE_DELETE
            # (e.g. Blender's main process polling a worker status file). Retry
            # briefly; the reader closes the file quickly.
            time.sleep(0.02 * (attempt + 1))
    temporary.unlink(missing_ok=True)
    # Windows last-resort: a non-atomic direct write keeps a progress/status
    # update from aborting the run. Readers (read_json) tolerate a partial JSON
    # by returning their default, so a momentary non-atomic write is safe.
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default
