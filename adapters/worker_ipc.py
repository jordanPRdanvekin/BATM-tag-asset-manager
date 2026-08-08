"""Versioned JSON IPC and worker command construction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import bpy

from .storage import atomic_json_write, ensure_dirs

IPC_SCHEMA_VERSION = 1


def worker_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "worker" / "batm_worker.py"


def prepare_request(run_id: str, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    run_dir = ensure_dirs()["runs"] / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{index:05d}"
    request_path = run_dir / f"request_{stem}.json"
    status_path = run_dir / f"status_{stem}.json"
    result_path = run_dir / f"result_{stem}.json"
    cancel_path = run_dir / "cancel.requested"
    request = {
        "schema_version": IPC_SCHEMA_VERSION,
        "run_id": run_id,
        "status_path": str(status_path),
        "result_path": str(result_path),
        "cancel_path": str(cancel_path),
        **payload,
    }
    atomic_json_write(request_path, request)
    return {
        "request": request,
        "request_path": str(request_path),
        "status_path": str(status_path),
        "result_path": str(result_path),
        "cancel_path": str(cancel_path),
    }


def command_for(prepared: dict[str, Any]) -> list[str]:
    return [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "11",
        "--python",
        str(worker_script_path()),
        "--",
        prepared["request_path"],
    ]


def request_cancel(prepared_jobs: list[dict[str, Any]]) -> None:
    for job in prepared_jobs:
        path = Path(job["cancel_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.touch(exist_ok=True)
        except OSError:
            pass
