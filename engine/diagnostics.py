"""Dataset diagnostics derived without changing Blender data."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.models import AssetSnapshot, TagOperation
from ..core.sanitizer import TAG_MAX_LENGTH, sanitize_tags


def summarize(snapshots: Iterable[AssetSnapshot], operations: Iterable[TagOperation]) -> dict[str, int]:
    total = 0
    empty = 0
    invalid = 0
    duplicate = 0
    cleaning = 0
    unique: set[str] = set()
    for snapshot in snapshots:
        seen: set[str] = set()
        for tag in snapshot.tags:
            total += 1
            if not str(tag).strip():
                empty += 1
            if len(tag) > TAG_MAX_LENGTH or any(ord(char) < 32 for char in tag):
                invalid += 1
            key = str(tag).casefold()
            if key in seen:
                duplicate += 1
            seen.add(key)
            if key:
                unique.add(key)
        sanitized, _errors = sanitize_tags(snapshot.tags)
        if sanitized != snapshot.tags:
            cleaning += 1
    return {
        "total_tags": total,
        "unique_tags": len(unique),
        "duplicate_tags": duplicate,
        "empty_tags": empty,
        "invalid_tags": invalid,
        "assets_requiring_cleaning": cleaning,
        "pending_operations": sum(1 for operation in operations if operation.enabled),
    }
