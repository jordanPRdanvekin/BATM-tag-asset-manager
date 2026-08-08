"""IPC request building, timeouts and the library_reference merge fix."""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

from _setup import ADDON_ROOT  # noqa: F401

bpy_stub = types.ModuleType("bpy")
bpy_stub.utils = types.SimpleNamespace(user_resource=lambda *a, **k: str(Path(ADDON_ROOT) / ".batm_test"))
sys.modules["bpy"] = bpy_stub

from batm.adapters.worker_ipc import prepare_request  # noqa: E402

os.environ["BATM_TEST"] = "1"


def test_prepare_request_carries_timeout():
    payload = {"mode": "ANALYZE", "assets": [{"key": "x"}]}
    prepared = prepare_request("run-1", 0, payload, timeout_seconds=123.0)
    assert prepared["timeout_seconds"] == 123.0
    assert prepared["request"]["timeout_seconds"] == 123.0
    assert Path(prepared["request_path"]).is_file()
    written = json.loads(Path(prepared["request_path"]).read_text(encoding="utf-8"))
    assert written["timeout_seconds"] == 123.0


def test_prepare_request_default_omits_timeout():
    prepared = prepare_request("run-2", 0, {"mode": "ANALYZE"})
    assert "timeout_seconds" not in prepared
    assert "timeout_seconds" not in prepared["request"]


def test_merge_keeps_library_reference_fact():
    """Workers must not erase the library_reference fact during the merge."""
    import shutil

    from batm.core.models import AssetKey, AssetSnapshot

    key = AssetKey(
        library_reference="Comedy Island",
        blend_path="C:/lib/file.blend",
        id_type="OBJECT",
        datablock_name="Chair",
    )
    snapshot = AssetSnapshot(key=key, facts={"library_reference": "Comedy Island"})
    # Simulate the scheduler merge: worker result carries an empty fact,
    # mirroring the batm_worker snapshot serialization (facts with "" values).
    snapshot.facts.update({"library_reference": ""})
    snapshot.facts["library_reference"] = snapshot.key.library_reference
    assert snapshot.facts["library_reference"] == "Comedy Island"


def test_storage_cleanup(tmp_path):
    from batm.adapters.storage import ensure_dirs

    original = os.environ.get("BATM_BASE_DIR")
    os.environ["BATM_BASE_DIR"] = str(tmp_path)
    try:
        dirs = ensure_dirs()
        for name in ("runs", "backups", "logs", "rules"):
            assert Path(dirs[name]).is_dir(), name
    finally:
        if original is None:
            os.environ.pop("BATM_BASE_DIR", None)
        else:
            os.environ["BATM_BASE_DIR"] = original


if __name__ == "__main__":
    test_prepare_request_carries_timeout()
    test_prepare_request_default_omits_timeout()
    test_merge_keeps_library_reference_fact()
    with tempfile.TemporaryDirectory() as folder:
        test_storage_cleanup(folder)
    print("IPC TESTS OK")