"""In-memory session controller. No state is stored in Scene data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import AssetSnapshot, DesiredAssetState, Rule, TagOperation
from .reducer import compile_states
from .sanitizer import tag_char_length
from .state_machine import transition


def filtered_added_tags(
    states: list[DesiredAssetState],
    search: str = "",
    length: int = 0,
) -> list[tuple[str, int]]:
    """Unique proposed Add-step Tags with coverage, filtered and sorted.

    Single source of truth for the Review → Add step: aggregation, free-text
    (manual) search and exact character-length filter live here so the UI and the
    pagination operator can never diverge.
    """
    agg: dict[str, tuple[str, int]] = {}
    for state in states:
        seen: set[str] = set()
        for tag in state.added:
            cf = tag.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            display, count = agg.get(cf, (tag, 0))
            agg[cf] = (display, count + 1)
    items = sorted(agg.values(), key=lambda item: item[0].casefold())
    search = (search or "").casefold().strip()
    length = int(length or 0)
    if not search and length <= 0:
        return items
    return [
        (display, count)
        for display, count in items
        if (not search or search in display.casefold())
        and (length <= 0 or tag_char_length(display) == length)
    ]


def filtered_final_tags(
    states: list[DesiredAssetState],
    search: str = "",
    length: int = 0,
) -> list[tuple[str, int]]:
    """Unique final-state Tags with coverage, filtered and sorted.

    Single source of truth for the Review last-step Final Tags preview: the UI
    and the pagination operator share this so they can never diverge.
    """
    agg: dict[str, tuple[str, int]] = {}
    for state in states:
        seen: set[str] = set()
        for tag in state.after:
            cf = tag.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            display, count = agg.get(cf, (tag, 0))
            agg[cf] = (display, count + 1)
    items = sorted(agg.values(), key=lambda item: item[0].casefold())
    search = (search or "").casefold().strip()
    length = int(length or 0)
    if not search and length <= 0:
        return items
    return [
        (display, count)
        for display, count in items
        if (not search or search in display.casefold())
        and (length <= 0 or tag_char_length(display) == length)
    ]


def filter_tag_list(
    frequency: list[tuple[str, int]],
    search: str = "",
    length: int = 0,
) -> list[tuple[str, int]]:
    """Filter a ``(name, count)`` Tag list by free-text search and exact length.

    Single source of truth for the Manual Tag Editor filters; shared by the UI,
    the pagination operator and Select All so they always agree on visibility.
    Input order is preserved.
    """
    search = (search or "").casefold().strip()
    length = int(length or 0)
    if not search and length <= 0:
        return list(frequency)
    return [
        (name, count)
        for name, count in frequency
        if (not search or search in name.casefold())
        and (length <= 0 or tag_char_length(name) == length)
    ]


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
    # Cached list of on-disk recovery backups (newest last), refreshed at
    # register time and after every backup mutation. Kept in the session so the
    # UI reads a pre-computed value instead of touching the filesystem in draw().
    recoverable_backup_paths: list[str] = field(default_factory=list)

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

    def reset(self) -> None:
        """Reset the transient run state (reload / re-register safe).

        Keeps ``rules`` (reloaded separately by ``register``) and the scheduler
        reference, but cancels any active scheduler and clears every transient
        collection so a reload or disable/enable never leaves a phantom Review,
        stale snapshots or a dangling worker behind.
        """
        self.phase = "IDLE"
        self.run_id = ""
        self.started_at = ""
        self.snapshots.clear()
        self.operations.clear()
        self.desired.clear()
        self.selected_tags.clear()
        self.active_review_token = ""
        self.messages.clear()
        if self.scheduler is not None:
            try:
                self.scheduler.cancel()
            except Exception:
                pass
            self.scheduler = None
        self.backup_path = ""
        self.sanitize_options = None

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
