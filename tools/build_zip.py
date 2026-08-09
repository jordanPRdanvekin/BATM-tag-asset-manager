"""Build the installable BATM add-on ZIP for Blender 5.x.

The ZIP contains the extension at its root (blender_manifest.toml at the top
level) so Blender can install it directly from disk. The exclusion list
mirrors ``blender_manifest.toml``'s ``paths_exclude_pattern``.

Usage:  python tools/build_zip.py [output_dir]
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {".git", "__pycache__", "tests", "docs", ".github", ".batm_test"}
EXCLUDE_EXTENSIONS = {".zip", ".pyc", ".pyo", ".log"}


def iter_package_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDE_EXTENSIONS:
            continue
        if path.name == "SSOT batmblendermanager.txt":
            continue
        if path.name.startswith("BATM_analisis"):
            continue
        files.append(path)
    return files


def build(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = ROOT / "blender_manifest.toml"
    if not manifest.is_file():
        raise SystemExit("blender_manifest.toml missing; aborting build")
    files = iter_package_files()
    if not any(path.name == "blender_manifest.toml" for path in files):
        raise SystemExit("blender_manifest.toml not part of the package; aborting")
    destination = output_dir / "batch_asset_tag_manager.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT))
    size_mb = destination.stat().st_size / (1024 * 1024)
    print(f"Built {destination} ({len(files)} files, {size_mb:.2f} MB)")
    return destination


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    build(output)