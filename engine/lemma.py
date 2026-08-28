"""Dictionary-based lemmatization replacing Porter Stemmer."""

from __future__ import annotations

import json
from pathlib import Path

LEMMA_MAP_PATH = Path(__file__).parents[1] / "resources" / "lemma_map.json"

_lemma_map: dict[str, str] | None = None


def load_lemma_map(path: str | Path | None = None) -> dict[str, str]:
    """Load lemmas JSON dict, casefold keys. Returns {} on error."""
    global _lemma_map
    if path is None and _lemma_map is not None:
        return _lemma_map
    try:
        source = Path(path) if path is not None else LEMMA_MAP_PATH
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    # Support both прямая dict and wrapped {"lemmas": {...}} or {"words": ...}
    if "lemmas" in payload and isinstance(payload["lemmas"], dict):
        raw = payload["lemmas"]
    elif len(payload) == 1 and isinstance(next(iter(payload.values())), dict):
        # Unwrap single nested dict if that's the map
        raw = next(iter(payload.values()))
        if not isinstance(raw, dict):
            raw = payload
    else:
        raw = payload
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        k = key.casefold().strip()
        v = value.lower().strip()
        if k and v:
            result[k] = v
    if path is None:
        _lemma_map = result
    return result


def lemma(word: str) -> str:
    """Casefold lookup in lemma map, fallback to identity lower."""
    m = load_lemma_map()
    key = str(word).casefold()
    return m.get(key, str(word).lower())


def lemmatize_token(token: str) -> str:
    """Lemmatize a token with plural stripping fallback."""
    low = str(token).lower()
    m = load_lemma_map()
    if low in m:
        return m[low]
    # Try stripping trailing 's' for English plurals
    if low.endswith("s") and len(low) > 3:
        base = low[:-1]
        if base in m:
            return m[base]
    return low
