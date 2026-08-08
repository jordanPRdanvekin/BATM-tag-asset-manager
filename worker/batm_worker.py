"""Static Blender background worker for BATM.

Executed by Blender, never imported by the interactive add-on. Communication is
restricted to versioned JSON files so no bpy object crosses a process boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import traceback
from pathlib import Path
from typing import Any

import bpy

# When Blender executes this file with `--python`, only `worker/` is added to
# sys.path. Add the add-on root so `..engine` package imports resolve.
_ADDON_ROOT = str(Path(__file__).resolve().parents[1])
if _ADDON_ROOT not in sys.path:
    sys.path.insert(0, _ADDON_ROOT)


def atomic_write(path: str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def read_json(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fingerprint(path: str) -> dict[str, Any]:
    source = Path(path)
    stat = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def same_fingerprint(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("size", "mtime_ns", "sha256"))


def id_type_of(datablock: Any) -> str:
    return str(datablock.bl_rna.identifier).upper()


def find_id(id_type: str, name: str) -> Any | None:
    expected = id_type.upper()
    for datablock in bpy.data.all_ids:
        if id_type_of(datablock) == expected and datablock.name == name:
            return datablock
    return None


def tag_names(datablock: Any) -> list[str]:
    asset_data = getattr(datablock, "asset_data", None)
    return [tag.name for tag in asset_data.tags] if asset_data else []


def set_tags(datablock: Any, desired: list[str]) -> None:
    asset_data = getattr(datablock, "asset_data", None)
    if asset_data is None:
        raise RuntimeError(f"Datablock is not marked as an asset: {datablock.name}")
    tags = asset_data.tags
    while len(tags):
        tags.remove(tags[0])
    for name in desired:
        if not name or len(name) > 63:
            raise ValueError(f"Invalid Tag length: {name!r}")
        tags.new(name)


def facts_for(datablock: Any) -> dict[str, Any]:
    """Deep facts using the specialized extractors.

    Uses an absolute import anchored at _ADDON_ROOT because this module is
    executed as a standalone script by Blender, so relative imports fail.
    """
    from engine.extractors import extract_all

    facts = extract_all(datablock)
    facts.setdefault("id_type", id_type_of(datablock))
    return facts


def open_blend(path: str) -> None:
    result = bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender could not open {path}")


def process_assets(request: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    assets = request.get("assets", [])
    output: list[dict[str, Any]] = []
    cancel_path = Path(request["cancel_path"])
    for index, item in enumerate(assets, start=1):
        if cancel_path.exists():
            raise InterruptedError("Cancellation requested")
        key = item["key"]
        datablock = find_id(key["id_type"], key["datablock_name"])
        if datablock is None:
            raise LookupError(f"Asset not found: {key['id_type']}::{key['datablock_name']}")
        current = tag_names(datablock)
        if mode == "ANALYZE":
            output.append({"key": key, "tags": current, "facts": facts_for(datablock)})
        else:
            expected = list(item.get("expected_tags", []))
            desired = list(item.get("desired_tags", []))
            if mode == "RESTORE" and current == desired:
                output.append({"key": key, "before": current, "after": desired, "already_restored": True})
                atomic_write(
                    request["status_path"],
                    {
                        "schema_version": 1,
                        "phase": mode,
                        "processed": index,
                        "total": len(assets),
                        "current_asset": key["datablock_name"],
                        "current_file": request.get("blend_path", ""),
                    },
                )
                continue
            if current != expected:
                raise RuntimeError(
                    f"Tag conflict for {key['id_type']}::{key['datablock_name']}: "
                    f"expected {expected!r}, found {current!r}"
                )
            set_tags(datablock, desired)
            output.append({"key": key, "before": current, "after": desired})
        atomic_write(
            request["status_path"],
            {
                "schema_version": 1,
                "phase": mode,
                "processed": index,
                "total": len(assets),
                "current_asset": key["datablock_name"],
                "current_file": request.get("blend_path", ""),
            },
        )
    return output


def count_assets_in_blend(path: str) -> int:
    count = 0
    with bpy.data.libraries.load(path, assets_only=True) as (data_from, _data_to):
        for attribute in dir(data_from):
            if attribute.startswith("_"):
                continue
            value = getattr(data_from, attribute, None)
            if isinstance(value, list):
                count += len(value)
    return count


def process_inventory(request: dict[str, Any]) -> dict[str, Any]:
    libraries: list[dict[str, Any]] = []
    cancel_path = Path(request["cancel_path"])
    for root_index, root_value in enumerate(request.get("roots", []), start=1):
        root = Path(root_value).resolve()
        count = 0
        files = 0
        errors: list[str] = []
        if root.is_dir():
            blend_paths = sorted(root.rglob("*.blend"))
        elif root.suffix.casefold() == ".blend" and root.exists():
            blend_paths = [root]
        else:
            blend_paths = []
            errors.append(f"Library root does not exist: {root}")
        for path in blend_paths:
            if cancel_path.exists():
                raise InterruptedError("Cancellation requested")
            try:
                if not (path.stat().st_mode & stat.S_IWRITE) or not os.access(path, os.W_OK):
                    errors.append(f"Read-only file excluded: {path}")
                    continue
                count += count_assets_in_blend(str(path))
                files += 1
            except Exception as exc:
                errors.append(f"{path}: {exc}")
            atomic_write(
                request["status_path"],
                {
                    "schema_version": 1,
                    "phase": "INVENTORY",
                    "library": str(root),
                    "processed_files": files,
                    "current_file": str(path),
                },
            )
        libraries.append(
            {
                "root": str(root),
                "name": root.name,
                "asset_count": count,
                "files": files,
                "errors": errors,
            }
        )
    return {"libraries": libraries}


def run(request: dict[str, Any]) -> dict[str, Any]:
    if int(request.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported BATM IPC schema")
    mode = str(request.get("mode", "")).upper()
    if mode == "INVENTORY":
        return {"success": True, **process_inventory(request)}

    blend_path = str(Path(request["blend_path"]).resolve())
    expected_fingerprint = request.get("fingerprint", {})
    actual_fingerprint = fingerprint(blend_path)
    if expected_fingerprint and not same_fingerprint(expected_fingerprint, actual_fingerprint):
        raise RuntimeError(f"File changed after Preview: {blend_path}")
    open_blend(blend_path)
    assets = process_assets(request, mode)
    if mode in {"APPLY", "RESTORE"}:
        if Path(request["cancel_path"]).exists():
            raise InterruptedError("Cancellation requested before save")
        result = bpy.ops.wm.save_as_mainfile(filepath=blend_path, check_existing=False)
        if "FINISHED" not in result:
            raise RuntimeError(f"Blender failed to save {blend_path}")
        open_blend(blend_path)
        for item in request.get("assets", []):
            key = item["key"]
            datablock = find_id(key["id_type"], key["datablock_name"])
            if datablock is None or tag_names(datablock) != list(item.get("desired_tags", [])):
                raise RuntimeError(f"Post-save verification failed: {key}")
    return {
        "success": True,
        "mode": mode,
        "blend_path": blend_path,
        "assets": assets,
        "fingerprint_after": fingerprint(blend_path),
        "saved": mode in {"APPLY", "RESTORE"},
        "verified": True,
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) != 1:
        return 2
    request = read_json(argv[0])
    try:
        result = run(request)
        atomic_write(request["result_path"], result)
        return 0
    except InterruptedError as exc:
        atomic_write(
            request["result_path"],
            {"success": False, "cancelled": True, "error": str(exc), "traceback": ""},
        )
        return 12
    except Exception as exc:
        atomic_write(
            request["result_path"],
            {"success": False, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 11


if __name__ == "__main__":
    raise SystemExit(main())
