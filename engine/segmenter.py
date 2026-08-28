"""Prudent compound-word segmentation (deterministic, no AI)."""

from __future__ import annotations

import json
from pathlib import Path

from .trie import Trie, build_trie, trie_segment

LEXICON_PATH = Path(__file__).resolve().parents[1] / "resources" / "segmenter_lexicon.json"
COMPOUND_SPLITS_PATH = Path(__file__).resolve().parents[1] / "resources" / "compound_splits.json"

_BASE_WORDS: frozenset[str] | None = None
_compound_splits: dict[str, list[str]] | None = None
_trie_cache: Trie | None = None
_trie_words_hash: int | None = None


def _load_base_words() -> frozenset[str]:
    """Load the segmenter lexicon from JSON. Falls back to a minimal seed on error."""
    global _BASE_WORDS
    if _BASE_WORDS is not None:
        return _BASE_WORDS
    try:
        payload = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
        words = {str(w).casefold() for w in payload.get("words", []) if str(w).strip()}
    except (OSError, ValueError):
        words = {
            "the", "and", "for", "but", "not", "you", "all", "can", "had", "her",
            "was", "one", "our", "out", "day", "get", "has", "him", "his", "how",
            "its", "may", "new", "now", "old", "see", "way", "who", "did", "get",
            "let", "say", "she", "too", "use",
        }
    _BASE_WORDS = frozenset(words)
    return _BASE_WORDS


def get_trie(lexicon: frozenset[str]) -> Trie:
    """Build/cache Trie from lexicon words."""
    global _trie_cache, _trie_words_hash
    h = hash(lexicon)
    if _trie_cache is not None and _trie_words_hash == h:
        return _trie_cache
    trie = build_trie(lexicon)
    _trie_cache = trie
    _trie_words_hash = h
    return trie


def _norm(tok: str) -> str:
    return "".join(ch for ch in tok.casefold() if ch.isalnum())


def load_compound_splits(path: str | Path | None = None) -> dict[str, list[str]]:
    """Load the curated ``exact token -> components`` thesaurus (deterministic).

    Entries are domain vocabulary, not engine logic: they are applied only on an
    exact normalized match and never guessed. Missing or broken resources yield
    an empty thesaurus so segmentation stays fully lexical.
    """
    global _compound_splits
    if path is None and _compound_splits is not None:
        return _compound_splits
    try:
        source = Path(path) if path is not None else COMPOUND_SPLITS_PATH
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fixups = payload.get("fixups", {}) if isinstance(payload, dict) else {}
    result = {
        _norm(str(key)): [str(word) for word in (value or []) if str(word).strip()]
        for key, value in fixups.items()
    }
    if path is None:
        _compound_splits = result
    return result


def collect_words(groups: list[dict], taxonomy: dict | None = None) -> frozenset[str]:
    """Build the segmenter lexicon from JSON base + knowledge groups + concept catalog."""
    words: set[str] = set(_load_base_words())
    for group in groups or []:
        for word in group.get("words", []) or []:
            w = _norm(str(word))
            if len(w) >= 2:
                words.add(w)
    if taxonomy:
        for domain in taxonomy.get("domains", []) or []:
            for category in domain.get("categories", []) or []:
                for word in category.get("words", []) or []:
                    w = _norm(str(word))
                    if len(w) >= 2:
                        words.add(w)
        for key, group in taxonomy.get("auxiliary", {}).items():
            for word in group.get("words", []) or []:
                w = _norm(str(word))
                if len(w) >= 2:
                    words.add(w)
        # Contextual terms (cub, pup, child...) are known catalog words too; they
        # must be valid split components (foxcub -> fox + cub).
        for term in taxonomy.get("contextual_terms", {}) or {}:
            w = _norm(str(term))
            if len(w) >= 2:
                words.add(w)
    return frozenset(words)


def _valid_parts(parts: list[str]) -> bool:
    """Only accept a split into meaningful words.

    Prudent segmentation: a real glued compound splits into words of >= 3
    characters (e.g. ``oak`` + ``tree``). Allowing 1-2 letter pieces would break
    ordinary words and proper nouns (``apron`` -> ``a`` + ``pron``,
    ``annette`` -> ``an`` + ``nette``), which is a correctness regression. When a
    token cannot split into such parts it is left unchanged (prudence).
    """
    return len(parts) >= 2 and all(len(word) >= 3 for word in parts)


def _known_split(token: str, lexicon: frozenset[str]) -> list[str] | None:
    """DP over known words. Returns the best full-coverage split or None."""
    n = len(token)
    dp: list[tuple[list, float] | None] = [None] * (n + 1)
    dp[0] = ([], 0.0)
    for i in range(1, n + 1):
        best = None
        for j in range(max(0, i - 28), i):
            if dp[j] is None:
                continue
            word = token[j:i]
            if word not in lexicon:
                continue
            score = dp[j][1] + len(word) * len(word) + (2.0 if len(word) >= 3 else 0.0)
            if best is None or score > best[1]:
                best = (dp[j][0] + [word], score)
        dp[i] = best
    if dp[n] is None:
        return None
    parts = dp[n][0]
    return parts if _valid_parts(parts) else None


_decompose_memo: dict[tuple[str, int, int], tuple[list[str], bool]] = {}

# Soft cap on memoized decompose results. Segmentation output depends only on the
# (normalized) token, ``max_edits`` and the lexicon source, all stable per session,
# so memoizing the expensive corrected-pass results is safe and avoids repeated
# work when many assets share the same word. The cap keeps memory bounded.
_DECOMPOSE_MEMO_MAX = 4096


def decompose(token: str, lexicon: frozenset[str], max_edits: int = 2, fixups: dict | None = None, sorted_words: list[str] | None = None) -> tuple[list[str], bool]:
    """Return ``(words, was_split)``. Never forces a split without evidence.

    ``fixups`` is an optional deterministic thesaurus (exact token -> words); it
    is applied only on an exact key match, never guessed. ``sorted_words`` is an
    optional pre-sorted lexicon kept for API compatibility with the earlier
    Levenshtein-correction pass.

    Segmentation is deliberately conservative. A contiguous token is split only
    when a full exact-lexicon decomposition exists (``_known_split``): every
    component must be a known word, so ``oaktree`` -> ``oak`` + ``tree``. A token
    with an explicit separator (``fire_force``) is not routed here — the
    sanitizer's separator/casing layer already produces the combined unit — and a
    token whose split would need a *corrected* (near-miss) component is preserved
    intact instead of gaining an invented boundary (``fireforce`` stays whole).
    """
    raw = str(token).strip()
    cleaned = _norm(raw)
    if not raw or len(cleaned) < 3:
        return [raw] if raw else [], False
    if fixups:
        fixed = fixups.get(raw.casefold()) or fixups.get(cleaned)
        if fixed:
            return [str(w) for w in fixed if str(w).strip()], True
    if cleaned in lexicon:
        return [raw], False
    # Segmentation output depends only on the normalized token and the lexicon
    # (via its id). Assets / Preview recompile pass the same cached lexicon and
    # often share names, so memoize the (cheap, exact) result per token.
    cache_key = (cleaned, max_edits, id(sorted_words if sorted_words is not None else lexicon))
    memo = _decompose_memo.get(cache_key)
    if memo is not None:
        return memo
    # Primary path: Trie segmentation O(L)
    try:
        trie = get_trie(lexicon)
        trie_parts = trie_segment(cleaned, trie)
        if trie_parts is not None:
            memo = (trie_parts, True)
            if len(_decompose_memo) >= _DECOMPOSE_MEMO_MAX:
                _decompose_memo.clear()
            _decompose_memo[cache_key] = memo
            return memo
    except Exception:
        pass
    parts = _known_split(cleaned, lexicon)
    memo = (parts, True) if parts is not None else ([raw], False)
    if len(_decompose_memo) >= _DECOMPOSE_MEMO_MAX:
        _decompose_memo.clear()
    _decompose_memo[cache_key] = memo
    return memo
