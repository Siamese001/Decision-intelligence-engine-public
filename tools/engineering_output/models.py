"""Data models and enums for engineering output governance."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionEnvironment(str, Enum):
    """Declared execution environment for observed effects."""

    LOCAL = "local"
    SANDBOX = "sandbox"
    PRODUCTION = "production"
    UNSPECIFIED = "unspecified"


class RouteType(str, Enum):
    """Route classification for turn response gating."""

    ENGINEERING = "engineering"
    QUERY = "query"
    COPILOT = "copilot"
    GOVERNANCE = "governance"
    OTHER = "other"


@dataclass
class Observation:
    """Behavior-specific runtime observation."""

    action: str
    input_description: str
    expected_effect: str
    observed_effect: str
    environment: ExecutionEnvironment = ExecutionEnvironment.LOCAL
    limitations: list[str] = field(default_factory=list)
    pre_state_digest: str | None = None
    post_state_digest: str | None = None
    source_files_hash: str | None = None
    attempt_number: int = 1
    is_success: bool = True
    is_rollback: bool = False
    rollback_approved_accessible: bool = False
    rollback_incompatible_rejected: bool = False
    timestamp_utc: str = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert observation to serializable dictionary."""
        return {
            "action": self.action,
            "input_description": self.input_description,
            "expected_effect": self.expected_effect,
            "observed_effect": self.observed_effect,
            "environment": self.environment.value,
            "limitations": list(self.limitations),
            "pre_state_digest": self.pre_state_digest,
            "post_state_digest": self.post_state_digest,
            "source_files_hash": self.source_files_hash,
            "attempt_number": self.attempt_number,
            "is_success": self.is_success,
            "is_rollback": self.is_rollback,
            "rollback_approved_accessible": self.rollback_approved_accessible,
            "rollback_incompatible_rejected": self.rollback_incompatible_rejected,
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class ValidationResult:
    """Outcome of engineering output and behavior validation."""

    is_valid: bool
    word_count: int = 0
    char_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert validation result to dictionary."""
        return {
            "is_valid": self.is_valid,
            "word_count": self.word_count,
            "char_count": self.char_count,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }
