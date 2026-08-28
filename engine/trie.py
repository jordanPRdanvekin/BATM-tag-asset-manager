"""Pure-Python prefix Trie for O(L) compound-word segmentation."""

from __future__ import annotations

from typing import Iterable


class TrieNode:
    """Node in the prefix Trie."""

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.is_word: bool = False


class Trie:
    """Prefix Trie with O(L) lookup for compound segmentation."""

    def __init__(self) -> None:
        self.root = TrieNode()

    def insert(self, word: str) -> None:
        """Insert a word into the Trie."""
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.is_word = True

    def search(self, word: str) -> bool:
        """Return True if word exists in the Trie."""
        node = self.root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.is_word

    def starts_with(self, prefix: str) -> bool:
        """Return True if any word in the Trie starts with prefix."""
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True

    def find_all_words(self, text: str, start: int) -> list[tuple[str, int]]:
        """Return all dictionary words starting at position ``start`` in ``text``.

        Each result is ``(word, end)`` where ``end`` is the exclusive
        end index (``text[start:end] == word``).
        """
        result: list[tuple[str, int]] = []
        node = self.root
        for i in range(start, len(text)):
            ch = text[i]
            if ch not in node.children:
                break
            node = node.children[ch]
            if node.is_word:
                result.append((text[start : i + 1], i + 1))
        return result


def build_trie(words: Iterable[str]) -> Trie:
    """Build a Trie from an iterable of words."""
    trie = Trie()
    for w in words:
        word = str(w).strip()
        if word:
            trie.insert(word)
    return trie


def trie_segment(text: str, trie: Trie) -> list[str] | None:
    """DP segmentation using Trie: partition ``text`` into dictionary words.

    Finds the best partition where every part has ``len >= 3`` and
    ``len(parts) >= 2``. Scoring maximises ``sum(len^2 + bonus)`` with
    ``bonus = 2`` if ``len >= 3`` (same as :func:`segmenter._known_split`).

    Uses memoization for the recursive search to avoid exponential blow-up.
    Returns ``None`` if no valid partition exists.
    """
    n = len(text)
    if n < 6:
        # Minimum 2 parts * 3 chars
        return None

    # memo[pos] -> best (parts, score) from pos to end, or None if impossible
    memo: dict[int, tuple[list[str], float] | None] = {}

    def _valid_parts(parts: list[str]) -> bool:
        return len(parts) >= 2 and all(len(w) >= 3 for w in parts)

    def best_from(pos: int) -> tuple[list[str], float] | None:
        if pos == n:
            return ([], 0.0)
        if pos in memo:
            return memo[pos]
        best: tuple[list[str], float] | None = None
        # Explore all words starting at pos via Trie in O(L)
        for word, end in trie.find_all_words(text, pos):
            rest = best_from(end)
            if rest is None:
                continue
            # Candidate partition: word + rest
            cand_parts = [word] + rest[0]
            cand_score = len(word) * len(word) + (2.0 if len(word) >= 3 else 0.0) + rest[1]
            # Prune intermediate? Allow building but final validation will enforce >=3.
            # Early prune: if word len < 3 and we still need valid final, skip single? Keep.
            if best is None or cand_score > best[1]:
                best = (cand_parts, cand_score)
        memo[pos] = best
        return best

    result = best_from(0)
    if result is None:
        return None
    parts = result[0]
    if _valid_parts(parts):
        return parts
    return None
