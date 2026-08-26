"""Public Blender 5.2 Asset Browser access and local-file mutation."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import bpy

from ..core.models import AssetKey, AssetSnapshot, DesiredAssetState
from ..engine.autotag import enrich_basic_facts
from ..engine.extractors import extract_all
from ..engine.fingerprint import fingerprint_file


def _ref_name(ref: Any) -> str:
    """Extract a stable library name from a string or an AssetLibraryReference."""
    if not ref:
        return ""
    if isinstance(ref, str):
        return ref
    name = getattr(ref, "name", "")
    if name:
        return str(name)
    return ""


def _resolve_library_name_by_path(context: Any) -> str:
    """Match the selected asset's blend path against configured library roots."""
    try:
        configured = list(bpy.context.preferences.filepaths.asset_libraries)
        if not configured:
            return ""
        # Find the first selected asset's full path and match its root.
        for asset in selected_assets(context):
            path = _resolved_path(asset)
            if not path:
                continue
            path_obj = Path(path)
            # Prefer the longest root that contains the asset path.
            best: tuple[int, str] = (0, "")
            for library in configured:
                root = Path(bpy.path.abspath(library.path)).resolve()
                try:
                    if path_obj.is_relative_to(root):
                        length = len(root.parts)
                        if length > best[0]:
                            best = (length, str(getattr(library, "name", root.name)))
                except Exception:
                    continue
            if best[1]:
                return best[1]
    except Exception:
        pass
    return ""


def _context_library_reference(context: Any) -> str:
    # Preferred path: FileAssetSelectParams (documented Blender 5.2 API).
    params = getattr(getattr(context, "space_data", None), "params", None)
    ref = getattr(params, "asset_library_reference", None)
    if ref:
        name = _ref_name(ref)
        if name and name not in {"", "ALL", "All", "LOCAL"}:
            return name
    # Fallback: context.asset_library_reference.
    value = getattr(context, "asset_library_reference", None)
    if value:
        name = _ref_name(value)
        if name and name not in {"", "ALL", "All", "LOCAL"}:
            return name
    # Fallback: resolve by path from the selected asset.
    return _resolve_library_name_by_path(context)


def active_library_label(context: Any) -> str:
    return _context_library_reference(context) or "Unknown"


def selected_assets(context: Any) -> list[Any]:
    return list(getattr(context, "selected_assets", []) or [])


def selected_tag_frequency(context: Any) -> tuple[int, list[tuple[str, int]]]:
    assets = selected_assets(context)
    frequency: dict[str, tuple[str, int]] = {}
    for asset in assets:
        seen: set[str] = set()
        for tag in getattr(asset.metadata, "tags", []):
            key = tag.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            display, count = frequency.get(key, (tag.name, 0))
            frequency[key] = (display, count + 1)
    values = sorted(frequency.values(), key=lambda item: item[0].casefold())
    return len(assets), values


def selected_asset_keys(context: Any) -> list[AssetKey]:
    fallback_library = _context_library_reference(context)
    keys: list[AssetKey] = []
    for asset in selected_assets(context):
        keys.append(
            AssetKey(
                library_reference=_owner_name(asset, fallback_library),
                blend_path=_resolved_path(asset),
                id_type=str(getattr(asset, "id_type", "")).upper(),
                datablock_name=str(asset.name),
            )
        )
    return keys


def _owner_name(asset: Any, fallback: str) -> str:
    owner = getattr(asset, "owner_asset_library", None)
    return _ref_name(owner) or fallback


def _resolved_path(asset: Any) -> str:
    local_id = getattr(asset, "local_id", None)
    if local_id is not None and bpy.data.filepath:
        return str(Path(bpy.data.filepath).resolve())
    path = str(getattr(asset, "full_library_path", "") or "")
    return str(Path(bpy.path.abspath(path)).resolve()) if path else ""


def batm_preferences(context: Any) -> Any | None:
    """Centralized AddonPreferences lookup (single source of truth)."""
    try:
        for key, addon in context.preferences.addons.items():
            if key.endswith("batch_asset_tag_manager"):
                return addon.preferences
    except Exception:
        return None
    return None


def refresh_asset_browser(context: Any) -> bool:
    """Refresh every open Asset Browser window; True if at least one refreshed."""
    refreshed = False
    try:
        for window in context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type != "FILE_BROWSER" or getattr(area.spaces.active, "browse_mode", "") != "ASSETS":
                    continue
                region = next((item for item in area.regions if item.type == "WINDOW"), None)
                try:
                    with context.temp_override(window=window, screen=screen, area=area, region=region):
                        result = bpy.ops.asset.library_refresh()
                        refreshed = refreshed or "FINISHED" in result
                except RuntimeError:
                    continue
    except Exception:
        pass
    return refreshed


def _is_file_writable(path: str) -> bool:
    if not path or not Path(path).is_file():
        return False
    try:
        mode = Path(path).stat().st_mode
        return bool(mode & stat.S_IWRITE) and os.access(path, os.W_OK)
    except OSError:
        return False


def _local_facts(datablock: Any) -> dict[str, Any]:
    """Deep Current File facts using the same unified extractors as the worker.

    This gives Current File assets the same canonical contract (plural
    aggregates + singular aliases) that external files receive from the
    background worker, so AutoTag rules behave identically everywhere.
    """
    if datablock is None:
        return {}
    return extract_all(datablock)


def snapshot_selection(
    context: Any,
    defer_fingerprints: bool = False,
) -> list[AssetSnapshot]:
    fallback_library = _context_library_reference(context)
    fingerprint_cache: dict[str, dict[str, Any]] = {}
    snapshots: list[AssetSnapshot] = []
    current_path = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ""
    for asset in selected_assets(context):
        path = _resolved_path(asset)
        id_type = str(getattr(asset, "id_type", "")).upper()
        facts = _local_facts(getattr(asset, "local_id", None))
        key = AssetKey(
            library_reference=_owner_name(asset, fallback_library),
            blend_path=path,
            id_type=id_type,
            datablock_name=str(asset.name),
        )
        online = bool(getattr(asset, "is_online", False))
        library_text = key.library_reference.casefold()
        reason = ""
        if online:
            reason = "Online assets are not writable"
        elif "essential" in library_text:
            reason = "Blender Essentials are read-only"
        elif not path or not Path(path).is_file():
            reason = "Asset .blend file is missing"
        elif path == current_path and (not bpy.data.is_saved or bpy.data.is_dirty):
            reason = "Current file must be saved with no pending changes"
        elif path != current_path and not _is_file_writable(path):
            reason = "Asset .blend file is not writable"
        if (
            path
            and Path(path).is_file()
            and not defer_fingerprints
            and path not in fingerprint_cache
        ):
            try:
                fingerprint_cache[path] = fingerprint_file(path)
            except OSError:
                fingerprint_cache[path] = {}
        tags = [tag.name for tag in getattr(asset.metadata, "tags", [])]
        snapshot = AssetSnapshot(
            key=key,
            tags=tags,
            facts=facts,
            fingerprint=fingerprint_cache.get(path, {}),
            writable=not reason,
            excluded_reason=reason,
        )
        enrich_basic_facts(snapshot)
        snapshots.append(snapshot)
    return snapshots


def find_local_id(key: AssetKey) -> Any | None:
    expected = key.id_type.upper()
    fallback = None
    # Current-file assets live in this file, so prefer the non-linked datablock.
    # `bpy.data.all_ids` also exposes linked library datablocks; matching only
    # name + id_type could pick a homonymous linked asset and edit the wrong one.
    for datablock in bpy.data.all_ids:
        if str(datablock.bl_rna.identifier).upper() == expected and datablock.name == key.datablock_name:
            if getattr(datablock, "library", None) is None:
                return datablock
            if fallback is None:
                fallback = datablock
    return fallback


def current_tags(datablock: Any) -> list[str]:
    asset_data = getattr(datablock, "asset_data", None)
    return [tag.name for tag in asset_data.tags] if asset_data else []


def set_local_tags(datablock: Any, values: list[str]) -> None:
    asset_data = getattr(datablock, "asset_data", None)
    if asset_data is None:
        raise RuntimeError(f"Datablock is not an asset: {datablock.name}")
    while len(asset_data.tags):
        asset_data.tags.remove(asset_data.tags[0])
    for value in values:
        asset_data.tags.new(value)


def apply_current_file(states: list[DesiredAssetState]) -> None:
    if not bpy.data.is_saved or bpy.data.is_dirty:
        raise RuntimeError("Current file must be saved and clean before BATM can write it")
    # Two passes: validate every asset before mutating any datablock, so a
    # conflict never leaves a partially modified file behind.
    resolved: list[tuple[Any, DesiredAssetState]] = []
    for state in states:
        datablock = find_local_id(state.key)
        if datablock is None:
            raise LookupError(f"Current-file asset not found: {state.key.token}")
        if current_tags(datablock) != state.before:
            raise RuntimeError(f"Current-file Tag conflict: {state.key.token}")
        resolved.append((datablock, state))
    for datablock, state in resolved:
        set_local_tags(datablock, state.after)
    result = bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError("Blender failed to save the current file")
    for state in states:
        datablock = find_local_id(state.key)
        if datablock is None or current_tags(datablock) != state.after:
            raise RuntimeError(f"Current-file verification failed: {state.key.token}")
