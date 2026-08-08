"""Compile pending operations into exact desired tag states."""

from __future__ import annotations

from collections.abc import Iterable

from .models import AssetSnapshot, DesiredAssetState, TagOperation
from .sanitizer import legacy_clean_one, sanitize_tags


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

    origin_order = {"AUTO": 0, "MANUAL": 1, "PREVIEW": 2}
    sorted_operations = sorted(
        (operation for operation in operations if operation.enabled),
        key=lambda operation: (origin_order.get(operation.origin, 9), operation.priority),
    )
    for operation in sorted_operations:
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
        after, after_errors = sanitize_tags(raw_by_token[token], sanitize_options)
        tag_reasons: dict[str, list[str]] = {}
        for raw_value, explanation in raw_reasons[token]:
            canonical, _ = sanitize_tags([raw_value], sanitize_options)
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
