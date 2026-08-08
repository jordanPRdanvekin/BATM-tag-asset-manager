"""Atomic storage paths for user rules, backups, logs and run IPC."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import bpy


def base_dir() -> Path:
    path = bpy.utils.user_resource("CONFIG", path="batm", create=True)
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


def atomic_json_write(path: str | Path, value: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default
