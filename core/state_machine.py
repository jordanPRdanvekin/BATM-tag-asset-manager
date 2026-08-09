"""Strict BATM run-state transitions."""

from __future__ import annotations


TRANSITIONS: dict[str, set[str]] = {
    "IDLE": {"ANALYZING", "RESTORING"},
    "ANALYZING": {"REVIEW_READY", "FAILED", "IDLE"},
    "REVIEW_READY": {"APPROVED", "IDLE"},
    "APPROVED": {"BACKING_UP", "FAILED"},
    "BACKING_UP": {"BACKED_UP", "FAILED"},
    "BACKED_UP": {"EXECUTING", "FAILED"},
    "EXECUTING": {"CANCEL_REQUESTED", "SUCCEEDED", "PARTIAL_FAILED", "FAILED"},
    "CANCEL_REQUESTED": {"RESTORING", "RESTORED", "FAILED"},
    "PARTIAL_FAILED": {"RESTORING", "FAILED"},
    "FAILED": {"RESTORING", "REFRESHING", "IDLE"},
    "RESTORING": {"RESTORED", "FAILED"},
    "RESTORED": {"REFRESHING", "IDLE"},
    "SUCCEEDED": {"REFRESHING"},
    "REFRESHING": {"IDLE", "FAILED"},
}


class InvalidTransition(RuntimeError):
    pass


def transition(current: str, target: str) -> str:
    if target not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"Invalid BATM transition: {current} -> {target}")
    return target
