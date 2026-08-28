# Deprecated: prefer engine.lemma for exact dictionary lemmatization. Kept for fallback.
"""Vendored Porter Stemmer (public domain, ~1980 algorithm).

Pure-Python implementation with zero dependencies. Used as a lightweight
fallback for fuzzy tag matching when exact lexicon matches fail.
"""

from __future__ import annotations

_VOWELS = frozenset("aeiouy")


def _is_consonant(word: str, index: int) -> bool:
    ch = word[index]
    if ch in _VOWELS:
        return False
    if ch == "y":
        if index == 0:
            return True
        return not _is_consonant(word, index - 1)
    return True


def _measure(word: str) -> int:
    n = len(word)
    i = 0
    while i < n and not _is_consonant(word, i):
        i += 1
    count = 0
    while i < n:
        while i < n and _is_consonant(word, i):
            i += 1
        if i >= n:
            break
        count += 1
        while i < n and not _is_consonant(word, i):
            i += 1
    return count


def _contains_vowel(word: str) -> bool:
    return any(not _is_consonant(word, i) for i in range(len(word)))


def _ends_with_double(word: str) -> bool:
    n = len(word)
    if n < 2:
        return False
    return word[-1] == word[-2] and _is_consonant(word, n - 1)


def _ends_with_cvc(word: str) -> bool:
    if len(word) < 3:
        return False
    j = len(word) - 1
    return (
        _is_consonant(word, j)
        and not _is_consonant(word, j - 1)
        and _is_consonant(word, j - 2)
        and word[j] not in "wxy"
    )


def _replace_suffix(word: str, suffix: str, replacement: str) -> str:
    if word.endswith(suffix):
        return word[: -len(suffix)] + replacement
    return word


def stem(word: str) -> str:
    """Reduce a word to its Porter stem. Returns lowercase result."""
    w = word.lower().strip()
    if len(w) <= 2:
        return w
    if w.endswith("s"):
        if w.endswith("sses"):
            w = _replace_suffix(w, "sses", "ss")
        elif w.endswith("ies"):
            w = _replace_suffix(w, "ies", "i")
        elif not w.endswith("ss") and w.endswith("s"):
            w = w[:-1]
    if w.endswith("eed"):
        stem_part = w[:-3]
        if _measure(stem_part) > 0:
            w = stem_part + "ee"
    if w.endswith("ed") or w.endswith("ing"):
        if w.endswith("ed"):
            stem_part = w[:-2]
        else:
            stem_part = w[:-3]
        if _contains_vowel(stem_part):
            w = stem_part
            if w.endswith(("at", "bl", "iz")):
                w = w + "e"
            elif _ends_with_double(w) and w[-1] not in "lsz":
                w = w[:-1]
            elif _measure(w) == 1 and _ends_with_cvc(w):
                w = w + "e"
    if w.endswith("y") and _contains_vowel(w[:-1]):
        w = w[:-1] + "i"
    if _measure(w) > 0:
        for suffix, replacement in (
            ("ational", "ate"), ("tional", "tion"), ("enci", "ence"),
            ("anci", "ance"), ("izer", "ize"), ("abli", "able"),
            ("alli", "al"), ("entli", "ent"), ("eli", "e"),
            ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
            ("ator", "ate"), ("alism", "al"), ("iveness", "ive"),
            ("fulness", "ful"), ("ousness", "ous"), ("aliti", "al"),
            ("iviti", "ive"), ("biliti", "ble"),
        ):
            if w.endswith(suffix):
                stem_part = w[: -len(suffix)]
                if _measure(stem_part) > 0:
                    w = stem_part + replacement
                break
        for suffix in ("ational", "tional", "enci", "anci", "izer", "abli",
                        "alli", "entli", "eli", "ousli", "ization", "ation",
                        "ator", "alism", "iveness", "fulness", "ousness",
                        "aliti", "iviti", "biliti", "entli"):
            pass
        for suffix, replacement in (
            ("alize", "al"), ("icate", "ic"), ("ative", ""), ("al", ""),
            ("ful", ""), ("ness", ""),
        ):
            if w.endswith(suffix):
                stem_part = w[: -len(suffix)]
                if _measure(stem_part) > 0:
                    w = stem_part + replacement
                break
        if w.endswith("e"):
            stem_part = w[:-1]
            if _measure(stem_part) > 1:
                w = stem_part
            elif _measure(stem_part) == 1 and not _ends_with_cvc(stem_part):
                w = stem_part
    if _measure(w) > 1 and _ends_with_double(w) and w[-1] not in "lsz":
        w = w[:-1]
    if w.endswith("ll") and _measure(w) > 1:
        w = w[:-1]
    return w
