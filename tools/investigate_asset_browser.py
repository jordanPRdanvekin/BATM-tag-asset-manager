"""BATM Blender 5.x validation checklist.

Paste this file into Blender's Python console with the Asset Browser open and
a library selected:

    exec(open(r"C:\\...\\tools\\investigate_asset_browser.py").read())

The script prints one PASS/FAIL line per API contract BATM depends on. It
modifies NOTHING. Copy the output and paste it into the chat.
"""

from __future__ import annotations

import os
from pathlib import Path

import bpy


class Checklist:
    """Collective PASS/FAIL reporter."""

    def __init__(self) -> None:
        self.total = 0
        self.passed = 0
        self.failed = 0

    def run(self, label: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.passed += 1
            print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
        else:
            self.failed += 1
            print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def investigate() -> None:
    checks = Checklist()
    print("=" * 70)
    print("BATM Blender 5.x validation checklist")
    print(f"Blender: {bpy.app.version_string}")
    print("=" * 70)

    # 1. Asset Browser context.
    print("\n[1] Asset Browser context")
    spaces = [area.spaces.active for area in bpy.context.screen.areas if area.type == "FILE_BROWSER"]
    checks.run("Asset Browser area exists", bool(spaces), f"found {len(spaces)}")
    browse_mode = next((getattr(sp, "browse_mode", "") for sp in spaces if getattr(sp, "browse_mode", "")), "")
    checks.run("browse_mode == ASSETS", browse_mode == "ASSETS", str(browse_mode))
    params = next((getattr(sp, "params", None) for sp in spaces), None)
    checks.run("space.params present", params is not None)
    if params is not None:
        ref = getattr(params, "asset_library_reference", None)
        checks.run("params.asset_library_reference", ref is not None, str(ref))
        checks.run("params.asset_catalog_visibility", hasattr(params, "asset_catalog_visibility"))
        checks.run("params.filter_search", hasattr(params, "filter_search"))
        checks.run("params.filter_asset_types", hasattr(params, "filter_asset_types"))
    context_ref = getattr(bpy.context, "asset_library_reference", None)
    checks.run("context.asset_library_reference", context_ref is not None, str(context_ref))

    # 2. Libraries registered.
    print("\n[2] Asset libraries")
    try:
        libraries = list(bpy.context.preferences.filepaths.asset_libraries)
        writable = 0
        for lib in libraries:
            root = Path(bpy.path.abspath(lib.path))
            is_writable = root.is_dir() and os.access(root, os.W_OK)
            if is_writable:
                writable += 1
            print(f"      library: {lib.name} -> {root}  writable={is_writable}")
        checks.run("asset_libraries readable", True, f"{len(libraries)} registered")
        checks.run("at least one writable user library", writable >= 1, f"writable={writable}")
    except Exception as exc:
        checks.run("asset_libraries readable", False, str(exc))

    # 3. Selection / Asset Representation API.
    print("\n[3] Selected assets contract")
    try:
        selected = list(bpy.context.selected_assets)
        checks.run("context.selected_assets iterable", True, f"{len(selected)} selected")
    except Exception as exc:
        selected = []
        checks.run("context.selected_assets iterable", False, str(exc))
    asset = selected[0] if selected else None
    checks.run(
        "at least one asset selected (covers full pipeline)",
        asset is not None,
        "select assets to validate the full contract",
    )
    if asset is not None:
        checks.run("asset.id_type", bool(getattr(asset, "id_type", "")), str(getattr(asset, "id_type", "")))
        checks.run("asset.full_library_path", bool(getattr(asset, "full_library_path", "")))
        checks.run("asset.owner_asset_library", getattr(asset, "owner_asset_library", None) is not None)
        metadata = getattr(asset, "metadata", None)
        checks.run("asset.metadata present", metadata is not None)
        if metadata is not None:
            tags = getattr(metadata, "tags", None)
            checks.run(
                "metadata.tags has add/clear API",
                tags is not None and hasattr(tags, "add") and hasattr(tags, "clear"),
            )
            checks.run("metadata.tag has .name", all(hasattr(t, "name") for t in tags) if tags is not None else True)
        local = getattr(asset, "local_id", None)
        checks.run("asset.local_id", local is not None, str(getattr(local, "name", "") if local is not None else ""))
        checks.run("asset.data datablock", getattr(asset, "data", None) is not None)

    # 4. Write-path API availability (presence only, never executed).
    print("\n[4] Write / refresh API surface (presence only)")
    checks.run("bpy.ops.wm.save_as_mainfile", hasattr(bpy.ops.wm, "save_as_mainfile"))
    checks.run("bpy.ops.asset.library_refresh", hasattr(bpy.ops.asset, "library_refresh"))
    checks.run("bpy.app.timers.register", hasattr(bpy.app.timers, "register"))
    checks.run("bpy.utils.user_resource", hasattr(bpy.utils, "user_resource"))

    # 5. BATM storage root.
    print("\n[5] BATM storage")
    try:
        base = bpy.utils.user_resource("SCRIPTS")
        checks.run("Blender scripts user root", bool(base), str(base))
        batm_dir = Path(base) / "addons" / "batm"
        checks.run("BATM storage dir writable", batm_dir.is_dir() or os.access(base, os.W_OK), str(batm_dir))
    except Exception as exc:
        checks.run("BATM storage root", False, str(exc))

    print("\n" + "=" * 70)
    print(f"CHECKLIST: {checks.passed} PASS / {checks.failed} FAIL / {checks.total} total")
    print("Paste this full output into the chat when validating BATM.")
    print("=" * 70)


if __name__ == "__main__":
    investigate()