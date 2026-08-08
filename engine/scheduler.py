"""Non-blocking process scheduler polled from Blender's main thread."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.storage import read_json
from ..adapters.worker_ipc import command_for, request_cancel


def _available_memory_gib() -> int:
    if os.name != "nt":
        return 4
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return max(1, int(status.ullAvailPhys // (1024**3)))
    except Exception:
        return 4


def adaptive_worker_count(job_count: int, preference_limit: int = 4) -> int:
    cpu_limit = max(1, (os.cpu_count() or 2) // 4)
    memory_limit = max(1, _available_memory_gib() // 4)
    return max(1, min(4, preference_limit, max(1, job_count), cpu_limit, memory_limit))


@dataclass
class RunningJob:
    prepared: dict[str, Any]
    process: subprocess.Popen[Any]


@dataclass
class BatchScheduler:
    pending: list[dict[str, Any]]
    max_workers: int
    running: list[RunningJob] = field(default_factory=list)
    completed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False

    def start_available(self) -> None:
        while (
            not self.cancel_requested
            and not self.failed
            and self.pending
            and len(self.running) < self.max_workers
        ):
            prepared = self.pending.pop(0)
            try:
                process = subprocess.Popen(
                    command_for(prepared),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
                self.running.append(RunningJob(prepared=prepared, process=process))
            except OSError as exc:
                self.failed.append(
                    {
                        "prepared": prepared,
                        "returncode": -1,
                        "result": {"success": False, "error": f"Could not start Blender worker: {exc}"},
                    }
                )
                break

    def poll(self) -> None:
        for job in list(self.running):
            code = job.process.poll()
            if code is None:
                continue
            self.running.remove(job)
            result = read_json(job.prepared["result_path"], {}) or {}
            record = {"prepared": job.prepared, "returncode": code, "result": result}
            if code == 0 and result.get("success"):
                self.completed.append(record)
            else:
                self.failed.append(record)
        self.start_available()

    def cancel(self) -> None:
        self.cancel_requested = True
        request_cancel([job.prepared for job in self.running])

    @property
    def done(self) -> bool:
        return not self.running and (not self.pending or self.cancel_requested or bool(self.failed))

    @property
    def total(self) -> int:
        return len(self.pending) + len(self.running) + len(self.completed) + len(self.failed)

    @property
    def confirmed(self) -> int:
        return len(self.completed)

    @property
    def total_assets(self) -> int:
        prepared_jobs = list(self.pending)
        prepared_jobs.extend(job.prepared for job in self.running)
        prepared_jobs.extend(record["prepared"] for record in self.completed)
        prepared_jobs.extend(record["prepared"] for record in self.failed)
        return sum(len(job.get("request", {}).get("assets", [])) for job in prepared_jobs)

    @property
    def confirmed_assets(self) -> int:
        return sum(len(record.get("result", {}).get("assets", [])) for record in self.completed)

    def latest_status(self) -> dict[str, Any]:
        if not self.running:
            return {}
        return read_json(self.running[0].prepared["status_path"], {}) or {}
