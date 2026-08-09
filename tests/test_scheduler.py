"""Scheduler watchdog/timeout test (no Blender required)."""

import sys
import time
import types

from _setup import ADDON_ROOT  # noqa: F401

bpy_stub = types.ModuleType("bpy")
bpy_stub.utils = types.SimpleNamespace(user_resource=lambda *a, **k: ADDON_ROOT + r"\.batm_test")
sys.modules["bpy"] = bpy_stub

from batm.engine import scheduler  # noqa: E402

scheduler.command_for = lambda prepared: ["python", "-c", "import time; time.sleep(60)"]
scheduler.read_json = lambda path, default=None: default
scheduler.request_cancel = lambda jobs: None

from batm.engine.scheduler import BatchScheduler  # noqa: E402


def test_timeout_kills_stuck_worker():
    pending = [
        {
            "result_path": "C:/tmp/result.json",
            "status_path": "C:/tmp/status.json",
            "request": {"assets": ["a"]},
            "timeout_seconds": 1,
        }
    ]
    sched = BatchScheduler(pending=pending, max_workers=1)
    sched.start_available()
    assert sched.running, "worker should start"
    time.sleep(1.4)
    sched.poll()
    assert not sched.running, "stuck worker must be removed"
    assert not sched.completed
    assert len(sched.failed) == 1, "stuck worker must land in failed"
    error = sched.failed[0]["result"].get("error", "")
    assert "deadline" in error and "killed" in error, f"unexpected error text: {error}"
    print("Scheduler watchdog OK")


def test_no_timeout_when_fast():
    pending = [
        {
            "result_path": "worker:/tmp/result.json",
            "status_path": "worker:/tmp/status.json",
            "request": {"assets": ["a1"]},
            "timeout_seconds": 30,
        }
    ]
    sched = BatchScheduler(pending=pending, max_workers=1)
    sched.start_available()
    time.sleep(0.2)
    sched.poll()
    assert sched.running, "fast job should still run within its deadline"
    assert not sched.failed
    sched.cancel()
    print("Scheduler ok with generous deadline")


if __name__ == "__main__":
    test_timeout_kills_stuck_worker()
    test_no_timeout_when_fast()
    print("SCHEDULER TESTS OK")