"""Atomic, checksummed Tag backup manifests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bpy

from ..adapters.storage import atomic_json_write, ensure_dirs, read_json
from ..core.models import AssetSnapshot, DesiredAssetState
from .. import BATM_VERSION_STRING


def _checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_backup(
    run_id: str,
    snapshots: list[AssetSnapshot],
    desired: dict[str, DesiredAssetState],
) -> Path:
    assets = []
    for snapshot in snapshots:
        state = desired.get(snapshot.key.token)
        if state is None or not state.changed:
            continue
        assets.append(
            {
                "key": snapshot.key.to_dict(),
                "original_tags": list(state.before),
                "applied_tags": list(state.after),
                "fingerprint_before": dict(snapshot.fingerprint),
                "restore_state": "PENDING",
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "batm_version": BATM_VERSION_STRING,
        "blender_version": bpy.app.version_string,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "READY",
        "assets": assets,
    }
    payload["checksum"] = _checksum(payload)
    path = ensure_dirs()["backups"] / f"batm_backup_{run_id}.json"
    atomic_json_write(path, payload)
    verify_backup(path)
    return path


def verify_backup(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError("BATM backup is unreadable")
    expected = payload.pop("checksum", "")
    actual = _checksum(payload)
    payload["checksum"] = expected
    if not expected or expected != actual:
        raise RuntimeError("BATM backup checksum mismatch")
    return payload


def recoverable_backups() -> list[Path]:
    backups = list(ensure_dirs()["backups"].glob("batm_backup_*.json"))

    def sort_key(path: Path) -> float:
        # Atomic writes set the mtime when the backup is finalized, so mtime
        # ordering matches creation order without reading every manifest (this
        # helper runs on every panel redraw).
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(backups, key=sort_key)


def update_backup_states(path: str | Path, blend_paths: set[str], status: str) -> None:
    payload = verify_backup(path)
    payload.pop("checksum", None)
    for asset in payload.get("assets", []):
        if str(asset.get("key", {}).get("blend_path", "")) in blend_paths:
            asset["restore_state"] = status
    payload["status"] = status
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["checksum"] = _checksum(payload)
    atomic_json_write(path, payload)


def delete_backup(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)


def prune_backups(retention_days: int = 30) -> list[Path]:
    """Delete backups older than retention_days, always keeping the newest."""
    backups = recoverable_backups()
    if len(backups) <= 1:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed: list[Path] = []
    for path in backups[:-1]:
        payload = read_json(path, {})
        stamp = payload.get("created_at", "") if isinstance(payload, dict) else ""
        age = 0.0
        if stamp:
            try:
                age = datetime.fromisoformat(stamp).timestamp()
            except ValueError:
                age = path.stat().st_mtime
        else:
            age = path.stat().st_mtime
        if age < cutoff:
            delete_backup(path)
            removed.append(path)
    return removed
