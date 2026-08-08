"""Persistent structured run logging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..adapters.storage import atomic_json_write, ensure_dirs, read_json


def persist_run_log(run_id: str, messages: list[dict], summary: dict) -> Path:
    path = ensure_dirs()["logs"] / f"batm_run_{run_id}.json"
    atomic_json_write(path, {"schema_version": 1, "run_id": run_id, "summary": summary, "events": messages})
    return path


def export_text(json_path: str | Path, text_path: str | Path) -> None:
    payload = read_json(json_path, {}) or {}
    lines = [f"BATM run {payload.get('run_id', 'unknown')}", ""]
    for event in payload.get("events", []):
        lines.append(
            f"{event.get('timestamp', '')} [{event.get('severity', 'INFO')}] "
            f"{event.get('code', '')}: {event.get('message', '')}"
        )
    Path(text_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def prune_logs(days: int = 30, maximum: int = 50) -> None:
    paths = sorted(ensure_dirs()["logs"].glob("batm_run_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    for index, path in enumerate(paths):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if index >= max(1, maximum) or modified < cutoff:
            path.unlink(missing_ok=True)
