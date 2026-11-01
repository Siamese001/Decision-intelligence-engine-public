"""Constants, error classes, and primitive validators for L1 control."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

ACTION_TYPES = ("requirement_hybrid_retrieval", "stop")
TERMINAL_REASONS = (
    "L1_COMPLETE",
    "L1_EXPLICIT_GAP",
    "L1_UNAUTHORIZED",
    "L1_CONFLICT",
    "L1_NON_REFINABLE",
    "L1_NO_PROGRESS",
    "L1_BUDGET_EXHAUSTED",
    "L1_POLICY_BLOCKED",
    "L1_INTERNAL_INVALID",
)
LANES = ("approved", "candidate", "external_public")
OUTCOMES = ("covered", "explicit_gap", "unauthorized", "conflict", "excluded")


class L1ControlError(ValueError):
    """Stable, closed failure from a pure controller transition."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in TERMINAL_REASONS and not reason.startswith("L1_"):
            raise ValueError(f"unknown L1 reason: {reason}")
        self.reason = reason
        super().__init__(f"{reason}: {message}")


class GuardStatus(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class ProgressStatus(StrEnum):
    PROGRESS = "progress"
    NO_PROGRESS = "no_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise L1ControlError(
            "L1_INTERNAL_INVALID",
            f"{label} keys invalid; missing={missing}, unknown={unknown}",
        )


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise L1ControlError("L1_INTERNAL_INVALID", f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise L1ControlError("L1_INTERNAL_INVALID", f"{label} must be at least {minimum}")
    return value


def _require_digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise L1ControlError("L1_INTERNAL_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value
