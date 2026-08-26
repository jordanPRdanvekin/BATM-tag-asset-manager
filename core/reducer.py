"""Compile pending operations into exact desired tag states."""

from __future__ import annotations

from collections.abc import Iterable

from .models import AssetSnapshot, DesiredAssetState, TagOperation
from .sanitizer import legacy_clean_one, sanitize_tags

# Operation kind for an Add-step exclusion ("do not add this Tag"). It stops a
# proposed/auto Tag from entering an asset's final set without touching existing
# Tags, and is independent of the Remove step (it only applies when Add is on).
CANCEL_ADD = "CANCEL_ADD"


def _remove_casefold(tags: list[str], value: str) -> tuple[list[str], bool]:
    key = legacy_clean_one(value).casefold()
    output = [tag for tag in tags if legacy_clean_one(tag).casefold() != key]
    return output, len(output) != len(tags)


def compile_states(
    snapshots: Iterable[AssetSnapshot],
    operations: Iterable[TagOperation],
    sanitize_options: dict | None = None,
) -> dict[str, DesiredAssetState]:
    snapshots_by_token = {snapshot.key.token: snapshot for snapshot in snapshots}
    raw_by_token = {token: list(snapshot.tags) for token, snapshot in snapshots_by_token.items()}
    notes: dict[str, list[str]] = {token: [] for token in snapshots_by_token}
    raw_reasons: dict[str, list[tuple[str, str]]] = {token: [] for token in snapshots_by_token}
    warnings: dict[str, list[str]] = {token: [] for token in snapshots_by_token}

    san_opts = dict(sanitize_options or {})
    enable_add = bool(san_opts.get("enable_add", True))
    enable_remove = bool(san_opts.get("enable_remove", True))
    enable_cleanup = bool(san_opts.get("enable_cleanup", True))
    # Last Step (per-asset fine-tune): PREVIEW ADD/REMOVE operations only apply
    # while this stage is on. CANCEL_ADD is an Add-step concept and stays gated
    # by enable_add alone.
    enable_assets = bool(san_opts.get("enable_assets", True))
    # When the Cleanup step is off, do not normalize casing / split / recombine:
    # keep the Tags as produced by the Add and Remove steps (safe dedup only).
    clean_opts = san_opts
    if not enable_cleanup:
        clean_opts = dict(san_opts)
        clean_opts.update(
            {
                "casing": "NONE",
                "segment": False,
                "remove_isolated_numbers": False,
                "merge_synonyms": False,
                "sort": False,
                "max_length": 0,
            }
        )

    origin_order = {"AUTO": 0, "MANUAL": 1, "PREVIEW": 2}
    sorted_operations = sorted(
        (operation for operation in operations if operation.enabled),
        key=lambda operation: (origin_order.get(operation.origin, 9), operation.priority),
    )
    # Add-step exclusion ("do not add this Tag"): values excluded from being added,
    # per asset. Independent of the Remove step — an Add-step concept — so it is
    # gated only by enable_add and only stops a proposed/auto Tag from entering the
    # final set; it never removes an existing Tag.
    cancelled_adds: dict[str, set[str]] = {}
    if enable_add:
        for operation in sorted_operations:
            if operation.kind != CANCEL_ADD:
                continue
            for token in operation.targets:
                if token not in raw_by_token:
                    continue
                bucket = cancelled_adds.setdefault(token, set())
                for value in operation.values:
                    bucket.add(legacy_clean_one(value).casefold())
    # Verbatim (manual) values the user typed exactly; they take priority over
    # the sanitizer and are re-asserted verbatim after cleaning.
    verbatim_by_token: dict[str, list[str]] = {}
    for operation in sorted_operations:
        if not getattr(operation, "verbatim", False) or operation.kind not in ("ADD", "REPLACE"):
            continue
        for token in operation.targets:
            if token in raw_by_token:
                verbatim_by_token.setdefault(token, []).extend(operation.values)
    for operation in sorted_operations:
        if operation.kind == CANCEL_ADD:
            continue  # already applied via the Add-step exclusion above
        if operation.kind == "ADD" and not enable_add:
            continue
        if operation.kind in ("REMOVE", "REPLACE") and not enable_remove:
            continue
        if operation.origin == "PREVIEW" and not enable_assets:
            continue
        for token in operation.targets:
            if token not in raw_by_token:
                continue
            tags = raw_by_token[token]
            if operation.kind == "ADD":
                tags.extend(operation.values)
                if operation.explanation:
                    for value in operation.values:
                        raw_reasons[token].append((str(value), operation.explanation))
            elif operation.kind == "REMOVE":
                for value in operation.values:
                    tags, removed = _remove_casefold(tags, value)
                    if not removed:
                        notes[token].append(f"Remove had no effect: {value}")
            elif operation.kind == "REPLACE":
                tags, replaced = _remove_casefold(tags, operation.source_value)
                if replaced:
                    tags.extend(operation.values)
                else:
                    notes[token].append(f"Replace had no effect: {operation.source_value}")
            else:
                warnings[token].append(f"Unsupported operation: {operation.kind}")
            if operation.explanation:
                notes[token].append(operation.explanation)
            raw_by_token[token] = tags

    output: dict[str, DesiredAssetState] = {}
    for token, snapshot in snapshots_by_token.items():
        before = list(snapshot.tags)
        after, after_errors = sanitize_tags(raw_by_token[token], clean_opts)
        # Verbatim re-assertion: exact values the user typed take priority over
        # the sanitizer's normalization (casing, spacing trimming, splitting).
        # The user's explicit rename/edit wins even if sanitization altered it.
        for value in verbatim_by_token.get(token, []) or []:
            exact = str(value)
            if not exact or exact.casefold() == "":
                continue
            if any(ord(char) < 32 for char in exact):
                after_errors.append(f"Tag contains a control character: {exact!r}")
                continue
            if any(t == exact for t in after):
                continue
            if any(t.casefold() == exact.casefold() for t in after):
                after = [exact if t.casefold() == exact.casefold() else t for t in after]
            else:
                after.append(exact)
        # Apply the Add-step exclusion on the sanitized output so it matches the
        # values shown in the Review (e.g. "Oak Tree" from a split "oaktree").
        # Only a *newly added* instance is dropped: a Tag that already exists on
        # the asset (in ``before``) is always kept, so exclusion never removes an
        # existing Tag (option: "does not touch existing Tags").
        excluded = cancelled_adds.get(token)
        if excluded:
            existing = {legacy_clean_one(tag).casefold() for tag in before}
            after = [
                tag for tag in after
                if (legacy_clean_one(tag).casefold() not in excluded
                    or legacy_clean_one(tag).casefold() in existing)
            ]
        tag_reasons: dict[str, list[str]] = {}
        for raw_value, explanation in raw_reasons[token]:
            canonical, _ = sanitize_tags([raw_value], clean_opts)
            for tag in canonical:
                key = tag.casefold()
                reasons = tag_reasons.setdefault(key, [])
                if explanation not in reasons:
                    reasons.append(explanation)
        before_keys = {tag.casefold() for tag in before}
        after_keys = {tag.casefold() for tag in after}
        output[token] = DesiredAssetState(
            key=snapshot.key,
            before=before,
            after=after,
            added=[tag for tag in after if tag.casefold() not in before_keys],
            removed=[tag for tag in before if tag.casefold() not in after_keys],
            explanations=notes[token],
            tag_reasons=tag_reasons,
            warnings=after_errors + warnings[token],
            enabled=snapshot.writable,
        )
        if snapshot.excluded_reason:
            output[token].warnings.append(snapshot.excluded_reason)
    return output
