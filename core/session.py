"""In-memory session controller. No state is stored in Scene data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import AssetSnapshot, DesiredAssetState, Rule, TagOperation
from .reducer import compile_states
from .state_machine import transition


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
    review_page: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    scheduler: Any = None
    backup_path: str = ""
    execution_requests: list[dict[str, Any]] = field(default_factory=list)
    completed_requests: list[dict[str, Any]] = field(default_factory=list)

    def reset(self, keep_operations: bool = False) -> None:
        operations = self.operations if keep_operations else []
        rules = self.rules
        self.__dict__.update(SessionController().__dict__)
        self.operations = operations
        self.rules = rules

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
        self.desired = compile_states(self.snapshots.values(), self.operations)
        return self.desired

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
