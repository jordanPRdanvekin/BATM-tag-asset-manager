"""Bundled knowledge dictionary: canonical category Tags from deep asset facts.

The JSON resource lives in ``resources/autotag_knowledge.json`` and groups
collections of words under a canonical category Tag (e.g. ``Wood`` group
contains oak, maple, pine...). Runs read-only; there is intentionally no UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.models import AssetSnapshot
from .segmenter import collect_words, decompose, load_compound_splits

RESOURCE_PATH = Path(__file__).resolve().parents[1] / "resources" / "autotag_knowledge.json"


def load_knowledge(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load and validate the knowledge groups. Missing or broken resources yield []."""
    try:
        source = Path(path) if path is not None else RESOURCE_PATH
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    groups = []
    for group in payload.get("groups", []) if isinstance(payload, dict) else []:
        tag = str(group.get("tag", "")).strip()
        words = [str(word).strip() for word in group.get("words", []) if str(word).strip()]
        if tag and words:
            groups.append({"tag": tag, "words": words})
    return groups


def word_index(groups: list[dict[str, Any]]) -> dict[str, str]:
    """Map every knowledge word (casefolded) to its canonical category Tag."""
    index: dict[str, str] = {}
    for group in groups or []:
        for word in group.get("words", []) or []:
            index.setdefault(str(word).casefold(), str(group.get("tag", "")))
    return index


def _candidate_facts(snapshot: AssetSnapshot, groups: list[dict[str, Any]] | None = None) -> set[str]:
    """Casefolded word pool from the facts most likely to carry category terms.

    Glued compounds (``rosarojavioleta``, ``foxcub``) are widened with their
    curated fixup components and, when the knowledge lexicon knows them, their
    segmenter parts — so knowledge categories are matched by the atomic words,
    never blocked by an exact-token-only coincidence.
    """
    facts = snapshot.facts
    sources: list[Any] = [
        facts.get("name_tokens", []) or [],
        facts.get("material_names", []) or [],
        facts.get("object_types", []) or [],
        facts.get("object_names", []) or [],
        facts.get("geometry_node_names", []) or [],
    ]
    words: set[str] = set()
    for source in sources:
        for item in source:
            for word in str(item).split():
                token = "".join(char for char in word if char.isalnum())
                if token:
                    words.add(token.casefold())
    if not words:
        return words
    # Widen with compound components (exact fixup first, then lexical split).
    fixups = load_compound_splits()
    lexicon = collect_words(groups or []) if groups else frozenset()
    for token in list(words):
        try:
            parts, was_split = decompose(token, lexicon, fixups=fixups)
        except Exception:
            continue
        if was_split:
            words.update(str(part).casefold() for part in parts if len(str(part)) >= 2)
    return words


def knowledge_tags(snapshot: AssetSnapshot, groups: list[dict[str, Any]], limit: int = 6) -> list[tuple[str, str]]:
    """Categories matched by the knowledge words, as ``(tag, matched_word)`` pairs.

    Ordered the way DEVELOPING groups are declared, deduplicated, and capped to
    avoid tag flooding.
    """
    index = word_index(groups)
    if not index:
        return []
    candidates = _candidate_facts(snapshot, groups)
    if not candidates:
        return []
    seen: set[str] = set()
    tags: list[tuple[str, str]] = []
    for group in groups or []:
        tag = str(group.get("tag", "")).strip()
        if not tag or tag in seen:
            continue
        matched = next((word for word in group.get("words", []) if str(word).casefold() in candidates), None)
        if matched is not None:
            seen.add(tag)
            tags.append((tag, str(matched)))
        if len(tags) >= max(1, limit):
            break
    return tags