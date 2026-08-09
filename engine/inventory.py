"""AssetIndex: BATM's own library inventory built from .blend files.

Instead of asking Blender "how many assets are you showing?", BATM builds its
own index by walking every .blend in each configured library and reading only
the assets marked as Asset via bpy.data.libraries.load(..., assets_only=True).
The UI works against this index, never against repeated Blender queries.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy

from ..adapters.storage import read_json
from ..adapters.worker_ipc import command_for, prepare_request, request_cancel


@dataclass
class LibraryIndex:
    """Per-library inventory data."""
    name: str
    root: str
    blend_files: int = 0
    asset_count: int = 0
    errors: list[str] = field(default_factory=list)
    scanned_at: str = ""


@dataclass
class InventoryService:
    prepared: dict[str, Any] | None = None
    process: Any = None  # subprocess.Popen
    roots_signature: tuple[str, ...] = ()
    libraries: dict[str, LibraryIndex] = field(default_factory=dict)
    status: str = "Not scanned"
    last_scan: str = ""
    retry_after: float = 0.0

    def configured_roots(self) -> tuple[str, ...]:
        roots: list[str] = []
        try:
            for library in bpy.context.preferences.filepaths.asset_libraries:
                path = str(Path(bpy.path.abspath(library.path)).resolve())
                if path and Path(path).exists() and os.access(path, os.W_OK):
                    roots.append(path)
        except Exception:
            pass
        return tuple(sorted(set(roots)))

    def begin(self, force: bool = False) -> None:
        if self.process is not None:
            return
        if not force and time.monotonic() < self.retry_after:
            return
        roots = self.configured_roots()
        if not roots:
            self.roots_signature = ()
            self.libraries.clear()
            self.status = "No writable user libraries configured"
            return
        if not force and roots == self.roots_signature and self.libraries:
            return
        run_id = f"inventory-{uuid4().hex[:12]}"
        self.prepared = prepare_request(run_id, 0, {"mode": "INVENTORY", "roots": list(roots)})
        self.process = __import__("subprocess").Popen(
            command_for(self.prepared),
            stdin=__import__("subprocess").DEVNULL,
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
            shell=False,
        )
        self.roots_signature = roots
        self.status = "Scanning writable libraries..."

    def poll(self) -> None:
        if self.process is None or self.prepared is None:
            return
        if self.process.poll() is None:
            status = read_json(self.prepared["status_path"], {}) or {}
            current = status.get("current_file", "")
            if current:
                self.status = f"Scanning: {Path(current).name}"
            return
        result = read_json(self.prepared["result_path"], {}) or {}
        if self.process.returncode == 0 and result.get("success"):
            self.libraries = {
                str(item["root"]): LibraryIndex(
                    name=str(item.get("name", Path(item["root"]).name)),
                    root=str(item["root"]),
                    blend_files=int(item.get("files", 0)),
                    asset_count=int(item.get("asset_count", 0)),
                    errors=list(item.get("errors", [])),
                    scanned_at=datetime.now(timezone.utc).isoformat(),
                )
                for item in result.get("libraries", [])
            }
            self.status = "Inventory ready"
            self.last_scan = datetime.now(timezone.utc).isoformat()
        else:
            self.status = f"Inventory failed: {result.get('error', 'worker error')}"
            self.retry_after = time.monotonic() + 300.0
        self.process = None
        self.prepared = None

    def total(self) -> int:
        return sum(index.asset_count for index in self.libraries.values())

    def library_by_name(self, name: str) -> LibraryIndex | None:
        """Resolve a library index by its display name (case-insensitive)."""
        normalized = name.casefold()
        for index in self.libraries.values():
            if index.name.casefold() == normalized:
                return index
        return None

    def library_by_root(self, root: str) -> LibraryIndex | None:
        return self.libraries.get(str(Path(root).resolve()))

    def count_for_reference(self, reference: str) -> int | None:
        """Return the asset count for the active library, never the global total."""
        normalized = reference.casefold()
        if normalized in {"all", "all libraries", ""}:
            return self.total()
        if "essential" in normalized or "online" in normalized:
            return None
        index = self.library_by_name(reference)
        if index is not None:
            return index.asset_count
        # Fallback: match by root folder name.
        for lib_index in self.libraries.values():
            if Path(lib_index.root).name.casefold() == normalized:
                return lib_index.asset_count
        return None

    def blend_files_for(self, reference: str) -> int | None:
        index = self.library_by_name(reference)
        if index is not None:
            return index.blend_files
        for lib_index in self.libraries.values():
            if Path(lib_index.root).name.casefold() == reference.casefold():
                return lib_index.blend_files
        return None

    def shutdown(self) -> None:
        if self.process is not None and self.prepared is not None:
            request_cancel([self.prepared])
        self.process = None
        self.prepared = None


INVENTORY = InventoryService()


def inventory_timer() -> float:
    try:
        INVENTORY.poll()
        if INVENTORY.process is None:
            INVENTORY.begin()
    except Exception as exc:
        INVENTORY.status = f"Inventory error: {exc}"
    return 2.0