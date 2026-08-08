"""BATM v3.x LTS — Phase 0: Asset Browser API investigation and validation.

Run inside Blender 5.2's Python console with the Asset Browser open and a
library selected:

    exec(open(r"C:\\...\\tools\\investigate_asset_browser.py").read())

This script does NOT modify anything. It only prints information to validate
the Asset Browser API surface used by BATM.
"""

from __future__ import annotations

import bpy


def _safe_repr(value) -> str:
    try:
        return repr(value)
    except Exception as exc:
        return f"<repr failed: {exc}>"


def _safe_getattr(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception as exc:
        return f"<error: {exc}>"


def investigate() -> None:
    print("=" * 70)
    print("BATM Phase 0 — Asset Browser Investigation")
    print("=" * 70)

    # 1. Asset Browser editor context
    print("\n--- 1. Asset Browser context ---")
    for area in bpy.context.screen.areas:
        if area.type != "FILE_BROWSER":
            continue
        space = area.spaces.active
        print(f"  Area: {area.type}, browse_mode: {_safe_getattr(space, 'browse_mode')}")
        params = _safe_getattr(space, "params")
        print(f"  params type: {type(params)}")
        if params is not None:
            print(f"  params.asset_library_reference: {_safe_repr(_safe_getattr(params, 'asset_library_reference'))}")
            print(f"  params.asset_library_reference type: {type(_safe_getattr(params, 'asset_library_reference'))}")
            print(f"  params.asset_catalog_visibility: {_safe_repr(_safe_getattr(params, 'asset_catalog_visibility'))}")
            print(f"  params.filter_asset_types: {_safe_repr(_safe_getattr(params, 'filter_asset_types'))}")
            print(f"  params.filter_search: {_safe_repr(_safe_getattr(params, 'filter_search'))}")
            print(f"  params.filter_id: {_safe_repr(_safe_getattr(params, 'filter_id'))}")
            print(f"  params.filter_asset_library: {_safe_repr(_safe_getattr(params, 'filter_asset_library'))}")
            print("  Relevant params attributes:")
            for attr in dir(params):
                if any(key in attr.lower() for key in ("asset", "catalog", "filter", "library")):
                    try:
                        value = getattr(params, attr)
                        print(f"    {attr} = {_safe_repr(value)}")
                    except Exception as exc:
                        print(f"    {attr} = <error: {exc}>")

    # 2. context.asset_library_reference
    print("\n--- 2. context.asset_library_reference ---")
    ref = _safe_getattr(bpy.context, "asset_library_reference")
    print(f"  context.asset_library_reference: {_safe_repr(ref)}")
    print(f"  type: {type(ref)}")
    if ref is not None:
        print(f"  dir(ref): {[a for a in dir(ref) if not a.startswith('_')]}")
        for attr in dir(ref):
            if not attr.startswith("_"):
                try:
                    print(f"    ref.{attr} = {_safe_repr(getattr(ref, attr))}")
                except Exception as exc:
                    print(f"    ref.{attr} = <error: {exc}>")

    # 3. Registered libraries
    print("\n--- 3. Libraries in preferences ---")
    try:
        libraries = bpy.context.preferences.filepaths.asset_libraries
        print(f"  Library count: {len(libraries)}")
        for i, lib in enumerate(libraries):
            print(f"  [{i}] name={_safe_repr(lib.name)} path={_safe_repr(lib.path)}")
            print(f"      type={type(lib)}")
            for attr in dir(lib):
                if not attr.startswith("_") and attr not in ("bl_rna", "rna_type"):
                    try:
                        print(f"      lib.{attr} = {_safe_repr(getattr(lib, attr))}")
                    except Exception as exc:
                        print(f"      lib.{attr} = <error: {exc}>")
    except Exception as exc:
        print(f"  Error accessing asset_libraries: {exc}")

    # 4. Selected assets
    print("\n--- 4. Selected assets ---")
    try:
        selected = list(bpy.context.selected_assets)
        print(f"  Selected asset count: {len(selected)}")
        for asset in selected[:5]:
            print(f"  Asset: name={_safe_repr(asset.name)} id_type={_safe_repr(asset.id_type)}")
            print(f"    full_library_path={_safe_repr(asset.full_library_path)}")
            print(f"    full_path={_safe_repr(_safe_getattr(asset, 'full_path'))}")
            print(f"    is_online={_safe_repr(_safe_getattr(asset, 'is_online'))}")
            owner = _safe_getattr(asset, "owner_asset_library")
            print(f"    owner_asset_library={_safe_repr(owner)}")
            if owner is not None:
                print(f"    owner type={type(owner)}")
                for attr in dir(owner):
                    if not attr.startswith("_"):
                        try:
                            print(f"      owner.{attr} = {_safe_repr(getattr(owner, attr))}")
                        except Exception as exc:
                            print(f"      owner.{attr} = <error: {exc}>")
            local_id = _safe_getattr(asset, "local_id")
            print(f"    local_id={_safe_repr(local_id)}")
            metadata = _safe_getattr(asset, "metadata")
            if metadata is not None:
                print(f"    metadata.catalog_id={_safe_repr(_safe_getattr(metadata, 'catalog_id'))}")
                print(f"    metadata.tags={_safe_repr([t.name for t in _safe_getattr(metadata, 'tags', [])])}")
    except Exception as exc:
        print(f"  Error accessing selected_assets: {exc}")

    # 5. Active catalog
    print("\n--- 5. Active catalog ---")
    try:
        catalog = _safe_getattr(bpy.context, "asset_catalog")
        print(f"  context.asset_catalog: {_safe_repr(catalog)}")
        if catalog is not None:
            print(f"  catalog type={type(catalog)}")
            for attr in dir(catalog):
                if not attr.startswith("_"):
                    try:
                        print(f"    catalog.{attr} = {_safe_repr(getattr(catalog, attr))}")
                    except Exception as exc:
                        print(f"    catalog.{attr} = <error: {exc}>")
    except Exception as exc:
        print(f"  Error accessing asset_catalog: {exc}")

    # 6. Inventory: count assets in the active library
    print("\n--- 6. Active library inventory ---")
    try:
        active_ref = _safe_getattr(bpy.context, "asset_library_reference")
        active_name = None
        if active_ref is not None:
            active_name = _safe_getattr(active_ref, "name")
        print(f"  Active library (ref.name): {active_name}")

        for lib in bpy.context.preferences.filepaths.asset_libraries:
            import os
            from pathlib import Path

            root = Path(bpy.path.abspath(lib.path))
            if not root.exists():
                print(f"  {lib.name}: path does not exist ({root})")
                continue
            blend_files = list(root.rglob("*.blend"))
            total_assets = 0
            for blend in blend_files:
                try:
                    with bpy.data.libraries.load(str(blend), assets_only=True) as (data_from, _data_to):
                        for attr in dir(data_from):
                            if attr.startswith("_"):
                                continue
                            value = getattr(data_from, attr, None)
                            if isinstance(value, list):
                                total_assets += len(value)
                except Exception as exc:
                    print(f"    Error in {blend.name}: {exc}")
            print(f"  {lib.name}: {len(blend_files)} .blend files, {total_assets} total assets")
    except Exception as exc:
        print(f"  Error in inventory: {exc}")

    print("\n" + "=" * 70)
    print("Investigation complete. Copy this output and paste it into the chat.")
    print("=" * 70)


if __name__ == "__main__":
    investigate()