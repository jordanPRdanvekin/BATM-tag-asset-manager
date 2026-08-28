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
from collections import defaultdict

from ..core.models import AssetSnapshot
from .segmenter import collect_words, decompose, load_compound_splits

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
    # Ensure Spanish fauna aliases: crane/grulla both map to Bird
    # If catalog lacks crane/grulla, inject deterministic alias entries.
    for alias_term, alias_tags in (("crane", ["Bird"]), ("grulla", ["Bird"]), ("ciguena", ["Bird"]), ("cigüena", ["Bird"])):
        k = alias_term.casefold()
        if k not in index:
            index.setdefault(k, []).append(("alias", "", "", alias_tags))
        else:
            # Ensure Bird present without duplicating if already aliased to something else
            existing = index.get(k, [])
            has_bird = any("bird" in str(v).casefold() for entry in existing for v in (entry[3] if isinstance(entry[3], list) else [entry[3]]) )
            if not has_bird:
                # Append Bird as alias
                index[k].append(("alias", "", "", ["Bird"]))
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


def _widened_candidates(snapshot: AssetSnapshot, taxonomy: dict) -> set[str]:
    """Candidates plus glued-compound components (fixups + prudent segmenter)."""
    candidates = _candidate_tokens(snapshot)
    if not candidates:
        return candidates
    # Glued compounds (e.g. "foxcub") match nothing under exact-token matching,
    # which hides fauna evidence and lets contextual age terms fall back to their
    # human default. Recover the components with the same prudent segmenter used
    # by Cleanup (full exact-lexicon coverage required, never guessed), plus the
    # curated exact-match fixups (rosarojavioleta -> rosa/roja/violeta).
    lexicon = _taxonomy_lexicon(taxonomy)
    fixups = load_compound_splits()
    for token in [tok for tok in candidates if len(tok) >= 6]:
        words, was_split = decompose(token, lexicon, fixups=fixups)
        if was_split:
            candidates.update(words)
    return candidates


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
    # Spanish alias fallback
    if not hits and key in ("grulla", "ciguena", "cigüena"):
        hits.append({"kind": "alias", "tag": ["Bird"]})
    if not hits and key == "crane":
        hits.append({"kind": "alias", "tag": ["Bird"]})
    return hits


def is_faunal(snapshot: AssetSnapshot, taxonomy: dict | None) -> bool:
    """Mutex evidence: did any candidate resolve to the Fauna domain or a
    Bird-family alias (including Spanish aliases and glued compounds)?"""
    if not taxonomy:
        return False
    index = _word_index(taxonomy)
    candidates = _widened_candidates(snapshot, taxonomy)
    _spanish_map = {"grulla": "crane", "ciguena": "crane", "cigüena": "crane"}
    for tok in list(candidates):
        mapped = _spanish_map.get(tok)
        if mapped:
            candidates.add(mapped)
    for token in candidates:
        for kind, dom_id, dom_label, tag in index.get(token, []):
            if kind == "domain" and dom_id and str(dom_id).casefold() == "fauna":
                return True
            if kind == "alias":
                vals = tag if isinstance(tag, list) else [tag]
                for v in vals:
                    if str(v).casefold() == "bird":
                        return True
    return False


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
    candidates = _widened_candidates(snapshot, taxonomy)
    if not candidates:
        return []

    # Spanish alias handling for fauna: grulla/crane -> bird, ensure candidate expansion
    # If token is Spanish alias, also add its English counterpart to candidates for index matching.
    _spanish_map = {"grulla": "crane", "ciguena": "crane", "cigüena": "crane"}
    for tok in list(candidates):
        mapped = _spanish_map.get(tok)
        if mapped:
            candidates.add(mapped)
    # If crane present, ensure Bird domain evidence will be found via alias index
    # (handled via _word_index injection)

    matched: dict[str, list] = {}
    matched_domains: dict[str, str] = {}
    for token in candidates:
        for entry in index.get(token, []):
            kind, dom_id, dom_label, tag = entry
            matched.setdefault(token, []).append(entry)
            if kind == "domain" and dom_id:
                matched_domains[dom_id] = dom_label
            # Alias that resolves to Bird should also count as fauna domain evidence
            if kind == "alias":
                vals = entry[3] if isinstance(entry[3], list) else [entry[3]]
                for v in vals:
                    if str(v).casefold() == "bird":
                        # Ensure fauna domain is considered present for mutex scoring
                        # Find fauna domain label from taxonomy
                        for d in taxonomy.get("domains", []):
                            if str(d.get("id", "")).casefold() == "fauna":
                                matched_domains.setdefault("fauna", str(d.get("label", "Fauna")))
                                break
                        else:
                            matched_domains.setdefault("fauna", "Fauna")
    # Determine faunal presence: fauna domain matched (strict) OR nature with fauna alias
    faunal = "fauna" in matched_domains
    # MOTX: faunal domains set for suppression — when faunal True, suppress Human
    # Also consider nature as faunal-adjacent? Keep strict to fauna only per catalog,
    # but allow alias-driven fauna above.

    # Domain scoring: count tokens per domain, winner gets +50 if prefix matched.
    # Suppress losers below threshold.
    domain_scores: dict[str, int] = defaultdict(int)
    for token, entries in matched.items():
        for kind, dom_id, dom_label, tag in entries:
            if kind == "domain" and dom_id:
                domain_scores[dom_id] += 1
            elif kind == "alias":
                # alias tags like Bird imply fauna domain scoring
                vals = tag if isinstance(tag, list) else [tag]
                for v in vals:
                    # Map Bird/Mammal etc to fauna domain
                    # Find domain that owns this category tag
                    for d in taxonomy.get("domains", []):
                        for c in d.get("categories", []) or []:
                            if str(c.get("tag", "")).casefold() == str(v).casefold():
                                domain_scores[str(d.get("id", "")).casefold()] += 1
                                break
    # Prefix bonus: if datablock name prefix matches domain id/label, winner boost
    prefix = ""
    try:
        name = str(snapshot.key.datablock_name or "")
        if "_" in name:
            prefix = name.split("_", 1)[0].casefold()
        else:
            # Check name_tokens first token
            toks = snapshot.facts.get("name_tokens", []) or []
            if toks:
                prefix = str(toks[0]).casefold()
    except Exception:
        prefix = ""
    if prefix:
        for dom_id in list(domain_scores.keys()):
            # Find label for this id
            label = matched_domains.get(dom_id, "")
            if dom_id.casefold() == prefix or (label and label.casefold() == prefix):
                domain_scores[dom_id] += 50
            # Also check domain id prefix heuristics: SM_, SK_ etc map to architecture/furniture?
            # Not needed; keep simple
    # Also check prefix token against domain label via direct candidates
    # Winner determination
    winner_dom = None
    winner_score = 0
    if domain_scores:
        winner_dom = max(domain_scores, key=lambda k: domain_scores[k])
        winner_score = domain_scores[winner_dom]
    # Threshold for suppression: losers below 50% of winner score are suppressed
    # If winner_score is 0, no suppression
    threshold = winner_score * 0.5 if winner_score > 0 else 0
    suppressed_domains: set[str] = set()
    if winner_dom is not None and winner_score > 0:
        for dom_id, score in domain_scores.items():
            if dom_id != winner_dom and score < threshold:
                suppressed_domains.add(dom_id)
        # If faunal is winner, ensure human is suppressed regardless of threshold
        if faunal and "human" in domain_scores:
            suppressed_domains.add("human")

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Contextual age/stage terms are emitted via explicit rules only.
    # Enhanced handling for faunal and props/architecture domains.
    for token in sorted(candidates):
        if token not in contextual:
            continue
        entry = contextual[token]
        # Determine chosen tags with mutex fauna handling
        chosen: list[str] = []
        fauna_entry = entry.get("fauna")
        default_entry = entry.get("default", [])
        # Special handling for "old" when not faunal but props/architecture domain present
        is_props_arch = any(d in matched_domains for d in ("props", "architecture"))
        if token == "old" and not faunal and is_props_arch:
            # When old is used with props/architecture, map to weathered/vintage not elder/ancient
            chosen = ["weathered", "vintage"]
        elif faunal and fauna_entry is not None:
            chosen = list(fauna_entry) if isinstance(fauna_entry, list) else [str(fauna_entry)]
            # Ensure fauna domain tag injected for contextual faunal terms (child,baby,kid,elder,small,large,old)
            if token in ("child", "baby", "kid", "elder", "old", "small", "large", "pup", "cub"):
                dom_label = matched_domains.get("fauna")
                if dom_label and dom_label.casefold() not in seen:
                    seen.add(dom_label.casefold())
                    results.append((dom_label, f"Domain: {dom_label} (via '{token}')"))
            elif matched_domains.get("fauna") and fauna_entry:
                # For other tokens with fauna entry, also inject fauna domain
                dom_label = matched_domains.get("fauna")
                if dom_label and dom_label.casefold() not in seen:
                    seen.add(dom_label.casefold())
                    results.append((dom_label, f"Domain: {dom_label} (via '{token}')"))
        elif faunal and token in ("small", "large", "child", "baby", "kid", "elder", "old"):
            # Faunal True but no fauna key (e.g., small/large) — map default but still inject fauna domain
            chosen = list(default_entry) if isinstance(default_entry, list) else [str(default_entry)] if default_entry else []
            dom_label = matched_domains.get("fauna")
            if dom_label and dom_label.casefold() not in seen:
                seen.add(dom_label.casefold())
                results.append((dom_label, f"Domain: {dom_label} (via '{token}')"))
        else:
            chosen = list(default_entry) if isinstance(default_entry, list) else [str(default_entry)] if default_entry else []
            # For non-faunal props/architecture old already handled
            # No domain injection for non-faunal

        for tag in chosen or []:
            tag = str(tag).strip()
            if tag and tag.casefold() not in seen:
                seen.add(tag.casefold())
                results.append((tag, f"Contextual rule: '{token}'"))
        # Legacy fallback: if faunal and entry has fauna, ensure fauna domain tag (redundant with above)
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
            # Suppress internal infra domains (linguistic, blender_tech) — alias bridges only.
            if dom_id.casefold() in {"linguistic", "blender_tech"}:
                continue
            # Domain scoring suppression: skip suppressed losers
            if dom_id in suppressed_domains:
                continue
            # Fauna evidence wins: an animal that is also named a "character",
            # "hero" or "beast" is not Human. Suppress the whole Human domain
            # whenever a fauna category matched. (Stricter: also when alias triggered fauna)
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
