"""Deterministic tag parsing and the BATM Legacy Clean preset."""

from __future__ import annotations

import re
from collections.abc import Iterable

TAG_MAX_LENGTH = 63


def split_tag_input(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[;,]", value)
    else:
        parts = []
        for item in value:
            parts.extend(re.split(r"[;,]", str(item)))
    return [part for part in parts]


def legacy_clean_one(value: str) -> str:
    cleaned = str(value).replace("'", "").replace("_", " ").replace("-", " ")
    cleaned = " ".join(cleaned.strip().split())
    return cleaned.title() if cleaned else ""


def sanitize_tags(values: str | Iterable[str]) -> tuple[list[str], list[str]]:
    """Return sanitized tags and blocking validation errors.

    Values are never truncated. Unicode is preserved and case-insensitive
    duplicate detection uses Unicode-aware casefold.
    """

    cleaned: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for raw in split_tag_input(values):
        tag = legacy_clean_one(raw)
        if not tag:
            continue
        if len(tag) > TAG_MAX_LENGTH:
            errors.append(f"Tag exceeds {TAG_MAX_LENGTH} characters: {tag}")
            continue
        if any(ord(char) < 32 for char in tag):
            errors.append(f"Tag contains a control character: {tag!r}")
            continue
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(tag)
    cleaned.sort(key=lambda item: item.casefold())
    return cleaned, errors
