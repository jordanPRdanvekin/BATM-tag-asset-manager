"""User catalog overrides: aliases, categories and contextual terms.

Persisted at ``batm/user_taxonomy_override.json`` and merged over the bundled
catalog at runtime. This module reads/writes the user layer only; the bundled
catalog stays untouched (SSOT directive 3/4/28).
"""

from __future__ import annotations

from typing import Any

from .storage import atomic_json_write, ensure_dirs, read_json
from ..engine.taxonomy import load_taxonomy, merge_user_overrides


OVERRIDE_SCHEMA_VERSION = 1

_ACTIVE_CACHE = None


def invalidate_taxonomy_cache() -> None:
    global _ACTIVE_CACHE
    _ACTIVE_CACHE = None


def user_taxonomy_path():  # PathLike
    return ensure_dirs()["base"] / "user_taxonomy_override.json"


def load_user_overrides() -> dict[str, Any]:
    """Return the persisted override payload (empty dict when absent/invalid)."""
    payload = read_json(user_taxonomy_path(), None)
    if not isinstance(payload, dict):
        return {}
    return {
        "aliases": dict(payload.get("aliases", {}) or {}),
        "domains": list(payload.get("domains", []) or []),
        "contextual_terms": dict(payload.get("contextual_terms", {}) or {}),
        "auxiliary": dict(payload.get("auxiliary", {}) or {}),
    }


def save_user_overrides(payload: dict[str, Any]) -> None:
    data = {
        "schema_version": OVERRIDE_SCHEMA_VERSION,
        "aliases": dict(payload.get("aliases", {}) or {}),
        "domains": list(payload.get("domains", []) or []),
        "contextual_terms": dict(payload.get("contextual_terms", {}) or {}),
        "auxiliary": dict(payload.get("auxiliary", {}) or {}),
    }
    atomic_json_write(user_taxonomy_path(), data)
    invalidate_taxonomy_cache()


def add_alias(term: str, tags: list[str]) -> None:
    """Bind a user keyword to category/domain tags (deterministic)."""
    term = str(term).strip()
    if not term or not tags:
        return
    overrides = load_user_overrides()
    aliases = dict(overrides.get("aliases", {}) or {})
    aliases[term] = [str(t).strip() for t in tags if str(t).strip()]
    overrides["aliases"] = aliases
    save_user_overrides(overrides)


def active_taxonomy() -> dict[str, Any]:
    """Bundled catalog merged with persisted user overrides (runtime catalog)."""
    global _ACTIVE_CACHE
    if _ACTIVE_CACHE is None:
        _ACTIVE_CACHE = merge_user_overrides(load_taxonomy(), load_user_overrides())
    return _ACTIVE_CACHE
