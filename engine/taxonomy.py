"""Deterministic concept catalog matching (no AI).

The catalog (resources/taxonomy.json) is a local, versioned, explainable resource.
``taxonomy_tags`` matches tokens against ``domain -> category -> word`` entries and
returns ``(tag, reason)`` pairs. Cross-tagging can be enabled/disabled and is capped
to avoid tag explosion. ``catalog_mode`` selects between primary concepts (domain
labels only) and the full catalog (domain label + matched category).

This module is bpy-free so it can be unit-tested without Blender.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import AssetSnapshot
from .segmenter import collect_words, decompose

RESOURCE_PATH = Path(__file__).resolve().parents[1] / "resources" / "taxonomy.json"

# Cached segmentation lexicons per taxonomy object (a run loads one catalog).
# A stable object identity lets the segmenter memo work across assets.
_TAX_LEXICON_CACHE: dict[int, frozenset] = {}


def _taxonomy_lexicon(taxonomy: dict) -> frozenset:
    key = id(taxonomy)
    lexicon = _TAX_LEXICON_CACHE.get(key)
    if lexicon is None:
        lexicon = frozenset(collect_words([], taxonomy))
        if len(_TAX_LEXICON_CACHE) > 8:
            _TAX_LEXICON_CACHE.clear()
        _TAX_LEXICON_CACHE[key] = lexicon
    return lexicon


def load_taxonomy(path=None) -> dict:
    """Load the bundled catalog. Missing or broken resources yield an empty catalog."""
    try:
        source = Path(path) if path is not None else RESOURCE_PATH
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"schema_version": 1, "domains": [], "aliases": {}, "contextual_terms": {}}
    payload.setdefault("domains", [])
    payload.setdefault("aliases", {})
    payload.setdefault("contextual_terms", {})
    payload.setdefault("auxiliary", {})
    return payload


def merge_user_overrides(taxonomy: dict, user: dict) -> dict:
    """Merge a user override payload into the bundled catalog (deterministic)."""
    if not user:
        return taxonomy
    merged = {
        "schema_version": taxonomy.get("schema_version", 1),
        "domains": [dict(d) for d in taxonomy.get("domains", [])],
        "aliases": dict(taxonomy.get("aliases", {})),
        "contextual_terms": dict(taxonomy.get("contextual_terms", {})),
        "auxiliary": dict(taxonomy.get("auxiliary", {})),
    }
    merged["aliases"].update(user.get("aliases", {}))
    merged["contextual_terms"].update(user.get("contextual_terms", {}))
    # Append user categories to their domain (or create a new domain).
    domain_by_id = {d["id"]: d for d in merged["domains"]}
    for dom in user.get("domains", []):
        dom = dict(dom)
        existing = domain_by_id.get(dom.get("id"))
        if existing is not None:
            existing.setdefault("categories", []).extend(dom.get("categories", []))
        else:
            dom.setdefault("categories", [])
            merged["domains"].append(dom)
            domain_by_id[dom["id"]] = dom
    # Allow user to augment the auxiliary singleton groups.
    for key, group in user.get("auxiliary", {}).items():
        merged["auxiliary"][key] = group
    return merged


def _word_index(taxonomy: dict) -> dict[str, list]:
    """Map every catalog word (casefolded) to its matching entries."""
    index: dict[str, list] = {}
    for domain in taxonomy.get("domains", []):
        dom_id = str(domain.get("id", ""))
        dom_label = str(domain.get("label", ""))
        for category in domain.get("categories", []) or []:
            tag = str(category.get("tag", "")).strip()
            for word in category.get("words", []) or []:
                key = str(word).casefold()
                if key:
                    index.setdefault(key, []).append(("domain", dom_id, dom_label, tag))
    for key, group in taxonomy.get("auxiliary", {}).items():
        tag = str(group.get("tag", "")).strip()
        for word in group.get("words", []) or []:
            k = str(word).casefold()
            if k:
                index.setdefault(k, []).append(("aux", "", "", tag))
    for term, tags in taxonomy.get("aliases", {}).items():
        key = str(term).casefold()
        if key:
            index.setdefault(key, []).append(("alias", "", "", tags))
    return index


def _candidate_tokens(snapshot: AssetSnapshot) -> set[str]:
    """Casefolded word pool from the facts likely to carry catalog concepts."""
    facts = snapshot.facts
    sources = [
        facts.get("name_tokens", []) or [],
        facts.get("material_names", []) or [],
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
    return words


def search_term(taxonomy: dict, term: str) -> list[dict]:
    """Locate every catalog entry a term matches (used by AddonPreferences)."""
    key = str(term).strip().casefold()
    if not key:
        return []
    index = _word_index(taxonomy)
    hits = []
    for kind, dom_id, dom_label, tag in index.get(key, []):
        if kind == "alias":
            hits.append({"kind": "alias", "tag": tag})
        elif kind == "aux":
            hits.append({"kind": "group", "domain": "Auxiliary", "tag": tag})
        else:
            hits.append({"kind": "category", "domain": dom_label, "tag": tag})
    return hits


def taxonomy_tags(
    snapshot: AssetSnapshot,
    taxonomy: dict,
    catalog_mode: str = "FULL",
    cross_tagging: bool = True,
    limit: int = 8,
) -> list[tuple[str, str]]:
    """Return ``(tag, reason)`` pairs from the concept catalog.

    ``catalog_mode`` ``"CONCEPTS"`` emits only primary domain labels; ``"FULL"``
    also emits the matched category tags. Domain labels are injected only when
    cross-tagging is enabled. Output is capped at ``limit`` tags.
    """
    index = _word_index(taxonomy)
    contextual = taxonomy.get("contextual_terms", {})
    candidates = _candidate_tokens(snapshot)
    if not candidates:
        return []

    # Glued compounds (e.g. "foxcub") match nothing under exact-token matching,
    # which hides fauna evidence and lets contextual age terms fall back to their
    # human default. Recover the components with the same prudent segmenter used
    # by Cleanup (full exact-lexicon coverage required, never guessed).
    lexicon = _taxonomy_lexicon(taxonomy)
    for token in [tok for tok in candidates if len(tok) >= 6]:
        words, was_split = decompose(token, lexicon)
        if was_split:
            candidates.update(words)

    matched: dict[str, list] = {}
    matched_domains: dict[str, str] = {}
    for token in candidates:
        for entry in index.get(token, []):
            kind, dom_id, dom_label, tag = entry
            matched.setdefault(token, []).append(entry)
            if kind == "domain" and dom_id:
                matched_domains[dom_id] = dom_label
    faunal = "fauna" in matched_domains

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Contextual age/stage terms are emitted via explicit rules only.
    for token in sorted(candidates):
        if token not in contextual:
            continue
        entry = contextual[token]
        chosen = entry.get("fauna") if (faunal and "fauna" in entry) else entry.get("default", [])
        for tag in chosen or []:
            tag = str(tag).strip()
            if tag and tag.casefold() not in seen:
                seen.add(tag.casefold())
                results.append((tag, f"Contextual rule: '{token}'"))
        if faunal and "fauna" in entry and matched_domains.get("fauna"):
            dom_label = matched_domains["fauna"]
            if dom_label.casefold() not in seen:
                seen.add(dom_label.casefold())
                results.append((dom_label, f"Domain: {dom_label} (via '{token}')"))

    for token in sorted(candidates):
        for kind, dom_id, dom_label, tag in matched.get(token, []):
            if kind == "alias":
                for value in tag if isinstance(tag, list) else str(tag).split(","):
                    value = str(value).strip()
                    if value and value.casefold() not in seen:
                        seen.add(value.casefold())
                        results.append((value, f"Alias: '{token}'"))
                continue
            if kind == "aux":
                tag_name = str(tag).strip()
                if tag_name and tag_name.casefold() not in seen:
                    seen.add(tag_name.casefold())
                    results.append((tag_name, f"Catalog group: {tag_name} (matched '{token}')"))
                continue
            # Domain entry.
            # Fauna evidence wins: an animal that is also named a "character",
            # "hero" or "beast" is not Human. Suppress the whole Human domain
            # whenever a fauna category matched.
            if faunal and dom_id == "human":
                continue
            if cross_tagging and dom_label and dom_label.casefold() not in seen:
                seen.add(dom_label.casefold())
                results.append((dom_label, f"Domain: {dom_label} (matched '{token}')"))
            if catalog_mode == "FULL" and tag and tag.casefold() not in seen:
                seen.add(tag.casefold())
                results.append((tag, f"Category: {tag} (matched '{token}')"))
        if len(results) >= max(1, limit):
            break
    return results
