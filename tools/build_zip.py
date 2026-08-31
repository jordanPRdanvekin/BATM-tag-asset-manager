#!/usr/bin/env python3
"""Build the installable BATM Extension ZIP (manifest at archive root).

The Blender Extensions CLI (``blender --command extension build``) is the
canonical builder, but CI runners may not have Blender. This fallback uses
plain Python so ``release.yml`` never breaks when Blender is absent. It
honours the manifest's ``[build].paths_exclude_pattern`` semantics loosely:
excludes dev-only files and VCS artefacts, keeps ``blender_manifest.toml``
at the zip root (required by the Extensions platform).
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import zipfile
from pathlib import Path

# Repository root = parent of this file's parent (``.../BATM tag asset manager``).
REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO_ROOT / "BATM tag asset manager"
MANIFEST = ADDON_DIR / "blender_manifest.toml"

DEFAULT_EXCLUDES = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".git",
    ".github",
    ".batm_test",
    ".pytest_cache",
    "*.zip",
    "tests",
    "docs",
]


def manifest_version() -> str:
    text = MANIFEST.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(.*?)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not parse version from blender_manifest.toml")
    return match.group(1).strip()


def should_exclude(relative: Path) -> bool:
    parts = relative.parts
    name = relative.name
    for pattern in DEFAULT_EXCLUDES:
        # Direct name match or any parent dir match
        if fnmatch.fnmatch(name, pattern):
            return True
        if pattern in parts:
            return True
        # fnmatch on full relative posix path
        if fnmatch.fnmatch(relative.as_posix(), pattern):
            return True
        if fnmatch.fnmatch(relative.as_posix(), f"*/{pattern}"):
            return True
    # Hard excludes from manifest build table (single files at root)
    if name in {
        "SSOT batmblendermanager.txt",
        "BATM_analisis_tecnico_preimplementacion.md",
        ".gitattributes",
        ".gitignore",
        ".editorconfig",
        "pyproject.toml",
    }:
        return True
    return False


def build(dist_dir: Path) -> Path:
    dist_dir.mkdir(parents=True, exist_ok=True)
    version = manifest_version()
    # Manifest version must stay 3.0.0 per project policy.
    if version != "3.0.0":
        raise RuntimeError(f"Manifest version is {version!r}, expected 3.0.0")
    if not MANIFEST.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST}")
    zip_name = f"batch_asset_tag_manager-{version}.zip"
    # Also keep legacy pattern expected by release.yml glob
    legacy_name = f"BATM_{version}.zip"
    target = dist_dir / zip_name
    legacy = dist_dir / legacy_name

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in ADDON_DIR.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(ADDON_DIR)
            if should_exclude(rel):
                continue
            # Store with manifest at archive root: contents of ADDON_DIR become zip root.
            archive.write(path, arcname=rel.as_posix())

        # Sanity: manifest must be at root
        if "blender_manifest.toml" not in archive.namelist():
            raise RuntimeError("blender_manifest.toml missing from archive — build is not installable")

    # Duplicate as legacy name for release.yml glob ``dist/BATM_*.zip``
    if target != legacy:
        legacy.write_bytes(target.read_bytes())
        print(f"Built {target} ({target.stat().st_size} bytes) + {legacy.name}")
    else:
        print(f"Built {target} ({target.stat().st_size} bytes)")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build BATM installable ZIP")
    parser.add_argument("dist", nargs="?", default="dist", help="Output directory (default: dist)")
    args = parser.parse_args()
    dist_dir = (REPO_ROOT / args.dist) if not Path(args.dist).is_absolute() else Path(args.dist)
    build(dist_dir)


if __name__ == "__main__":
    main()
