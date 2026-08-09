"""Shared bootstrap: make the add-on package importable without Blender.

Run the test scripts from anywhere with ``python tests/test_*.py``.
"""
import sys
import types
from pathlib import Path

ADDON_ROOT = str(Path(__file__).resolve().parents[1])

pkg = types.ModuleType("batm")
pkg.__path__ = [ADDON_ROOT]
sys.modules["batm"] = pkg
sys.path.insert(0, ADDON_ROOT)