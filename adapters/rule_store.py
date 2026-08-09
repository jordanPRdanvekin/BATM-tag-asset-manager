"""User rule persistence with immutable bundled defaults."""

from __future__ import annotations

from pathlib import Path

from ..core.models import Rule
from ..core.rules import load_rules, rules_payload
from .storage import atomic_json_write, ensure_dirs


def default_rules_path() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "autotag_rules.json"


def user_rules_path() -> Path:
    return ensure_dirs()["rules"] / "autotag_rules.json"


def load_active_rules() -> list[Rule]:
    custom = user_rules_path()
    if custom.exists():
        try:
            return load_rules(custom)
        except (OSError, ValueError):
            pass
    return load_rules(default_rules_path())


def save_active_rules(rules: list[Rule]) -> Path:
    return atomic_json_write(user_rules_path(), rules_payload(rules))


def reset_rules() -> list[Rule]:
    custom = user_rules_path()
    custom.unlink(missing_ok=True)
    return load_rules(default_rules_path())
