"""Deterministic tag parsing, cleaning and normalization.

``sanitize_tags`` accepts an options dict so the same engine serves the
Manual Editor, AutoTag and the Preview pipeline. Defaults reproduce the
historic BATM preset exactly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

TAG_MAX_LENGTH = 63

SEPARATOR_PATTERNS = {
    "COMMA": re.compile(r"[,]+"),
    "SEMICOLON": re.compile(r"[;]+"),
    "COMMA_SEMICOLON": re.compile(r"[;,]+"),
    "SPACE": re.compile(r"\s+"),
    "PIPE": re.compile(r"[|]+"),
}

CASING_MODES = ("NONE", "LOWER", "UPPER", "TITLE", "SNAKE", "CAMEL", "PASCAL", "KEBAB")

_WORD_SPLIT = re.compile(r"[\s_\-']+")
_TRAILING_NUMBER = re.compile(r"[\s_\-]?\d+$")


def split_tag_input(value: str | Iterable[str], separators: str = "COMMA_SEMICOLON") -> list[str]:
    pattern = SEPARATOR_PATTERNS.get(str(separators).upper(), SEPARATOR_PATTERNS["COMMA_SEMICOLON"])
    if isinstance(value, str):
        return [part for part in pattern.split(value)]
    parts: list[str] = []
    for item in value:
        parts.extend(pattern.split(str(item)))
    return parts


def legacy_clean_one(value: str) -> str:
    """Canonical form used for REMOVE/REPLACE matching (unchanged behavior)."""
    cleaned = str(value).replace("'", "").replace("_", " ").replace("-", " ")
    cleaned = " ".join(cleaned.strip().split())
    return cleaned.title() if cleaned else ""


def _apply_casing(tag: str, mode: str) -> str:
    if mode == "NONE":
        return tag
    words = [word for word in _WORD_SPLIT.split(tag) if word]
    if not words:
        return ""
    if mode == "TITLE":
        return " ".join(word.capitalize() for word in words)
    if mode == "LOWER":
        return " ".join(word.lower() for word in words)
    if mode == "UPPER":
        return " ".join(word.upper() for word in words)
    if mode == "SNAKE":
        return "_".join(word.lower() for word in words)
    if mode == "KEBAB":
        return "-".join(word.lower() for word in words)
    if mode == "PASCAL":
        return "".join(word.capitalize() for word in words)
    if mode == "CAMEL":
        return words[0].lower() + "".join(word.capitalize() for word in words[1:])
    # Unknown or "NONE" fall back to the historical preset.
    return " ".join(word.capitalize() for word in words)


def _strip_trailing_number(tag: str) -> str:
    return _TRAILING_NUMBER.sub("", tag).rstrip() if tag else tag


def default_options() -> dict:
    return {
        "separators": "COMMA_SEMICOLON",
        "casing": "TITLE",
        "sort": True,
        "max_length": TAG_MAX_LENGTH,
        "remove_isolated_numbers": False,
        "merge_synonyms": False,
        "blacklist": (),
        "synonyms": {},
        "max_tags": 0,
    }


def options_from_props(props: object) -> dict[str, object]:
    """Read the runtime sanitize properties into a pure options dict."""
    options = default_options()
    options["separators"] = str(getattr(props, "sanitize_separators", "COMMA_SEMICOLON"))
    options["casing"] = str(getattr(props, "sanitize_casing", "TITLE"))
    options["sort"] = bool(getattr(props, "sanitize_sort", True))
    options["max_length"] = int(getattr(props, "sanitize_max_length", TAG_MAX_LENGTH))
    options["remove_isolated_numbers"] = bool(getattr(props, "sanitize_remove_numbers", False))
    options["merge_synonyms"] = bool(getattr(props, "sanitize_merge_synonyms", False))
    options["max_tags"] = max(0, int(getattr(props, "sanitize_max_tags", 0)))
    options["blacklist"] = [
        part.casefold() for part in split_tag_input(str(getattr(props, "sanitize_blacklist", "")))
    ]
    if options["merge_synonyms"]:
        from ..engine.knowledge import load_knowledge

        options["synonyms"] = build_synonym_map(load_knowledge())
    return options


def sanitize_tags(values: str | Iterable[str], options: dict | None = None) -> tuple[list[str], list[str]]:
    """Split, normalize, deduplicate and sort Tags.

    Returns ``(cleaned, errors)``. Errors are blocking: the offending Tag is
    skipped. Over-length Tags are truncated to ``max_length``.
    """
    opts = {**default_options(), **(options or {})}
    separators = str(opts["separators"]).upper()
    casing = str(opts["casing"]).upper()
    if casing not in CASING_MODES:
        casing = "TITLE"
    max_length = max(0, int(opts.get("max_length", TAG_MAX_LENGTH)))
    sort_result = bool(opts.get("sort", True))
    remove_numbers = bool(opts.get("remove_isolated_numbers", False))
    merge_synonyms = bool(opts.get("merge_synonyms", False))
    synonyms = opts.get("synonyms") or {}
    blacklist = {str(item).casefold() for item in (opts.get("blacklist") or [])}
    max_tags = max(0, int(opts.get("max_tags", 0)))

    cleaned: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for raw in split_tag_input(values, separators):
        base = str(raw).replace("'", "").strip()
        if not base:
            continue
        tag = _apply_casing(base, casing)
        if not tag:
            continue
        if remove_numbers:
            if tag.isdigit():
                continue
            tag = _strip_trailing_number(tag)
            if not tag:
                continue
        requested = tag if not merge_synonyms else str(synonyms.get(tag.casefold(), tag))
        if max_length > 0 and len(requested) > max_length:
            requested = requested[:max_length]
        if not requested:
            continue
        if any(ord(char) < 32 for char in requested):
            errors.append(f"Tag contains a control character: {requested!r}")
            continue
        key = requested.casefold()
        if key in blacklist:
            continue
        if key not in seen:
            seen.add(key)
            cleaned.append(requested)
        if max_tags > 0 and len(cleaned) >= max_tags:
            break
    if sort_result:
        cleaned.sort(key=lambda item: item.casefold())
    return cleaned, errors


def build_synonym_map(groups: list[dict]) -> dict[str, str]:
    """Map every knowledge word to its canonical category Tag."""
    mapping: dict[str, str] = {}
    for group in groups or []:
        tag = str(group.get("tag", "")).strip()
        if not tag:
            continue
        for word in group.get("words", []) or []:
            mapping[str(word).casefold()] = tag
    return mapping