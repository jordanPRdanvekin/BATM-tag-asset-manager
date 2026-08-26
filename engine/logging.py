"""Persistent structured run logging and centralized failure diagnostics."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# Centralized failure diagnostics (safe, non-raising, with fallback)
# ---------------------------------------------------------------------------


def _addon_version_string() -> str:
    """Read the add-on version from the manifest without importing the package.

    The package root imports ``bpy`` on import, so version is derived from the
    bpy-free packaging source to keep diagnostics safe even at import time.
    """
    try:
        manifest = Path(__file__).resolve().parents[1] / "blender_manifest.toml"
        for line in manifest.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except (OSError, ValueError):
        pass
    return "unknown"


def _blender_version_string() -> str:
    try:
        import bpy  # local import: bpy may be unavailable during teardown

        return str(bpy.app.version_string)
    except Exception:
        return "unknown"


def _exc_location(exc: BaseException | None) -> tuple[str, str, str]:
    """Return (file, linenum, function) of the exception origin (best effort)."""
    tb = getattr(exc, "__traceback__", None) if exc is not None else sys.exc_info()[2]
    if tb is not None:
        try:
            frames = traceback.extract_tb(tb)
            if frames:
                last = frames[-1]
                return str(last.filename), str(last.lineno), str(last.name)
        except Exception:
            pass
    return "", "", ""


def capture_exception(
    *,
    code: str = "UNCAUGHT",
    message: str = "",
    operation: str = "",
    phase: str = "",
    context: str = "",
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Capture a recoverable failure into a structured, persisted record.

    Verbose evidence is recorded (file/function/line, traceback, active
    operation/phase and a probable cause). ``CAUSE`` is ``UNKNOWN`` when not
    derivable — only the available evidence is kept, never an invented cause.

    This helper is intentionally failure-proof: it never raises, and if the
    primary log destination fails it falls back to an alternate destination and
    finally to stderr, so error handling can never cascade into a second fault.
    """
    active = sys.exc_info()[1]
    caused_by = str(exc) if exc is not None else (str(active) if active is not None else "")
    file_name, line_no, function = _exc_location(exc)

    if exc is not None:
        traceback_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        exception_name = type(exc).__name__
    elif active is not None:
        traceback_text = "".join(traceback.format_exception(*sys.exc_info()))
        exception_name = type(active).__name__
    else:
        traceback_text = "(no active exception)"
        exception_name = ""

    record: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": "ERROR",
        "addon_version": _addon_version_string(),
        "blender_version": _blender_version_string(),
        "code": code,
        "message": message,
        "operation": operation,
        "phase": phase,
        "context": context,
        "file": file_name,
        "linenum": line_no,
        "function": function,
        "exception": exception_name,
        "cause": caused_by or "UNKNOWN",
        "traceback": traceback_text,
    }
    persist_diagnostic(record)
    return record


def persist_diagnostic(record: dict[str, Any]) -> Path | None:
    """Write a diagnostic record safely. Returns the path or None on failure."""
    try:
        stamp = str(record.get("timestamp", "")).replace(":", "").replace(".", "").replace("+", "_")
        path = ensure_dirs()["logs"] / f"diagnostic_{stamp}.json"
        atomic_json_write(path, record)
        return path
    except Exception:
        pass
    try:
        fallback = ensure_dirs()["base"] / "diagnostic_last_error.json"
        atomic_json_write(fallback, record)
    except Exception:
        try:
            sys.stderr.write(f"BATM diagnostic (log path unavailable): {record!r}\n")
        except Exception:
            pass
    return None

