"""Stable file fingerprints used to detect concurrent modifications."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def fingerprint_file(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    stat = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def fingerprints_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("size", "mtime_ns", "sha256"))
