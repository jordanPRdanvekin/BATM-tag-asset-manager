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
    "DOUBLE_HYPHEN": re.compile(r"--+"),
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


def tag_char_length(tag: str) -> int:
    """Number of letters/digits in a Tag (spaces, hyphens and symbols ignored).

    Used by the character-length tag filter (e.g. ``2`` shows only Tags with
    exactly two letters/digits). Pure-Python so it is shared by the UI panels
    without a ``bpy`` dependency.
    """
    return sum(1 for char in str(tag) if char.isalnum())


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


def apply_casing(tag: str, mode: str) -> str:
    """Public casing policy used by AutoTag for generated tags (SSOT)."""
    return _apply_casing(str(tag), str(mode))


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
        "segment": False,
        "segment_fixups": dict(_segment_fixups()),
        "recombine": "BOTH",
        "enable_add": True,
        "enable_remove": True,
        "enable_cleanup": True,
        "enable_assets": True,
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
    options["segment"] = bool(getattr(props, "sanitize_segment", False))
    options["recombine"] = str(getattr(props, "sanitize_recombine", "BOTH")).upper()
    options["segment_fixups"] = dict(_segment_fixups())
    options["enable_add"] = bool(getattr(props, "review_add_on", True))
    options["enable_remove"] = bool(getattr(props, "review_remove_on", True))
    options["enable_cleanup"] = bool(getattr(props, "review_cleanup_on", True))
    options["enable_assets"] = bool(getattr(props, "review_assets_on", True))
    options["blacklist"] = [
        part.casefold() for part in split_tag_input(str(getattr(props, "sanitize_blacklist", "")))
    ]
    if options["merge_synonyms"]:
        from ..engine.knowledge import load_knowledge

        options["synonyms"] = build_synonym_map(load_knowledge())
    return options




_SEGMENT_LEXICON: frozenset[str] | None = None
_SEGMENT_LEXICON_SORTED: list[str] | None = None


def _segment_lexicon() -> tuple[frozenset[str], list[str]]:
    """Return the cached segmenter lexicon plus its pre-sorted words.

    Building the lexicon reloads the Knowledge and taxonomy resources, so it is
    computed once per session and reused for every asset. Rebuilding it inside
    every ``sanitize_tags`` call was the dominant cost on large selections
    (thousands of assets with Split Compound Words ON) and froze the main thread.
    The pre-sorted list avoids re-sorting the whole lexicon for every compound
    token in ``segmenter``.
    """
    global _SEGMENT_LEXICON, _SEGMENT_LEXICON_SORTED
    if _SEGMENT_LEXICON is None:
        from ..engine.knowledge import load_knowledge
        from ..engine.taxonomy import load_taxonomy
        from ..engine.segmenter import collect_words
        words = collect_words(load_knowledge(), load_taxonomy())
        _SEGMENT_LEXICON = frozenset(words)
        _SEGMENT_LEXICON_SORTED = sorted(words, key=lambda w: (len(w), w))
    return _SEGMENT_LEXICON, _SEGMENT_LEXICON_SORTED


def _segment_fixups() -> dict:
    """Curated compound-word thesaurus used by Cleanup segmentation (data-driven)."""
    from ..engine.segmenter import load_compound_splits
    return load_compound_splits()

def sanitize_tags(values: str | Iterable[str], options: dict | None = None) -> tuple[list[str], list[str]]:
    """Split, normalize, deduplicate, optionally recombine and sort Tags.

    Returns ``(cleaned, errors)``. Errors are blocking: the offending Tag is
    skipped. Over-length Tags are truncated to ``max_length``.

    Pipeline:
        Raw Tags -> Split -> Normalize -> Remove Invalid/Empty -> Deduplicate
        -> Optional Recombine -> Final Tags

    When ``separators == "DOUBLE_HYPHEN"``, each part is additionally split on
    single hyphens so compound input like ``cc-rosse--red`` yields the
    individual components ``cc``, ``rosse``, ``red``. The ``recombine`` option
    then controls whether the individual Tags, the recombined Tag, or both are
    kept (default: both).
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
    segment = bool(opts.get("segment", False))
    recombine = str(opts.get("recombine", "BOTH")).upper()
    if recombine not in {"SPLIT", "RECOMBINED", "BOTH"}:
        recombine = "BOTH"

    # Split by the configured separators.
    parts = split_tag_input(values, separators)
    # Compound separator: DOUBLE_HYPHEN also splits each part on single hyphens
    # so "cc-rosse--red" yields "cc", "rosse", "red".
    if separators == "DOUBLE_HYPHEN":
        expanded: list[str] = []
        for part in parts:
            expanded.extend(str(part).split("-"))
        parts = expanded

    if segment:
        from ..engine.segmenter import decompose as _decompose
        lex, lex_sorted = _segment_lexicon()
        fixups = opts.get("segment_fixups") or {}
        expanded_parts = []
        for part in parts:
            raw = str(part)
            base = raw.replace("'", "").strip()
            sub_tokens = [tok for tok in _WORD_SPLIT.split(base) if tok] if len(base) >= 3 else []
            if not sub_tokens:
                expanded_parts.append(raw)
                continue
            # Decompose every sub-token on its own. A separator-joined part like
            # ``fire_force`` whose pieces are already proper words is NOT expanded
            # (the casing layer yields the single combined Tag "Fire Force"); the
            # part is only expanded when at least one sub-token is a real glued
            # compound (``mat_foxcub_eyecornea`` -> mat, fox, cub, eye, cornea).
            out: list[str] = []
            changed = False
            for tok in sub_tokens:
                if len(tok) >= 3:
                    words, was_split = _decompose(tok, lex, fixups=fixups, sorted_words=lex_sorted)
                    # A thesaurus entry that maps a token to itself (promesh ->
                    # promesh) is a preservation marker, not a split.
                    if was_split and [w.casefold() for w in words] != [tok.casefold()]:
                        changed = True
                        # Combined representation is the semantically correct unit —
                        # not always the raw token, not always hyphen-joined:
                        #   * a curated thesaurus split preserves the original token
                        #     (e.g. promeshchild -> promeshchild);
                        #   * a lexical-discovery split rejoins the validated
                        #     components through normal casing/separators
                        #     (e.g. oaktree -> "oak tree").
                        # The combined form is appended as plain text (never fed
                        # back into decomposition), so it cannot be re-segmented.
                        # Curated fixups are preservation markers (e.g. promesh ->
                        # promesh) unless the split yields 3+ components — in that
                        # case the spaced Title Case form is the correct combined
                        # unit (rosarojavioleta -> "Rosa Roja Violeta").
                        if tok.casefold() in fixups and len(words) > 2:
                            combined = " ".join(words)
                        else:
                            combined = tok if tok.casefold() in fixups else " ".join(words)
                        if recombine == "RECOMBINED":
                            if combined not in out:
                                out.append(combined)
                        elif recombine == "SPLIT":
                            out.extend(words)
                        else:  # BOTH: the valid combined unit plus the valid components.
                            out.extend(words)
                            if combined not in out:
                                out.append(combined)
                        continue
                out.append(tok)
            if changed:
                expanded_parts.extend(out)
            else:
                expanded_parts.append(raw)
        parts = expanded_parts

    individual: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for raw in parts:
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
            individual.append(requested)

    cleaned = list(individual)

    # Optional recombine: join the individual Tags with a single hyphen.
    if recombine in {"RECOMBINED", "BOTH"} and separators == "DOUBLE_HYPHEN" and individual:
        combined = "-".join(individual)
        if combined.casefold() not in seen:
            seen.add(combined.casefold())
            cleaned.append(combined)

    if recombine == "RECOMBINED" and separators == "DOUBLE_HYPHEN":
        cleaned = [item for item in cleaned if item not in individual]

    if sort_result:
        cleaned.sort(key=lambda item: item.casefold())

    if max_tags > 0:
        cleaned = cleaned[:max_tags]
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