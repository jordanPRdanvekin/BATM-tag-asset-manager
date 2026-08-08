"""In-memory session controller. No state is stored in Scene data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import AssetSnapshot, DesiredAssetState, Rule, TagOperation
from .reducer import compile_states
from .state_machine import transition


def filtered_review_states(search: str = "", filter_mode: str = "ALL") -> list[DesiredAssetState]:
    """Sorted, search- and filter-aware Review list. Single source of truth."""
    states = sorted(
        SESSION.desired.values(),
        key=lambda item: (item.key.datablock_name.casefold(), item.key.id_type),
    )
    if filter_mode == "CHANGED":
        states = [state for state in states if state.changed]
    elif filter_mode == "UNCHANGED":
        states = [state for state in states if not state.changed]
    elif filter_mode == "WARNINGS":
        states = [state for state in states if state.warnings]
    elif filter_mode == "INVALID":
        states = [state for state in states if state.changed and not state.valid]
    search = (search or "").casefold().strip()
    if not search:
        return states
    return [
        state
        for state in states
        if search in state.key.datablock_name.casefold()
        or search in state.key.id_type.casefold()
        or search in state.key.blend_path.casefold()
        or any(search in tag.casefold() for tag in state.after)
    ]


@dataclass
class SessionController:
    phase: str = "IDLE"
    run_id: str = ""
    started_at: str = ""
    snapshots: dict[str, AssetSnapshot] = field(default_factory=dict)
    operations: list[TagOperation] = field(default_factory=list)
    desired: dict[str, DesiredAssetState] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    selected_tags: set[str] = field(default_factory=set)
    active_review_token: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    scheduler: Any = None
    backup_path: str = ""
    sanitize_options: dict[str, Any] | None = None

    def begin(self) -> None:
        if self.phase != "IDLE":
            raise RuntimeError(f"BATM is busy: {self.phase}")
        self.phase = transition(self.phase, "ANALYZING")
        self.run_id = str(uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.desired.clear()
        self.messages.clear()

    def set_phase(self, value: str) -> None:
        self.phase = transition(self.phase, value)

    def compile(self) -> dict[str, DesiredAssetState]:
        self.desired = compile_states(self.snapshots.values(), self.operations, self.sanitize_options)
        return self.desired

    def recompile_preserving_disabled(self) -> None:
        """Recompile keeping user-disabled states disabled (single source of truth)."""
        disabled = {token for token, state in self.desired.items() if not state.enabled}
        self.compile()
        for token in disabled:
            if token in self.desired:
                self.desired[token].enabled = False

    def add_message(self, severity: str, code: str, message: str, **details: Any) -> None:
        self.messages.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "phase": self.phase,
                "severity": severity,
                "code": code,
                "message": message,
                "details": details,
            }
        )


SESSION = SessionController()
