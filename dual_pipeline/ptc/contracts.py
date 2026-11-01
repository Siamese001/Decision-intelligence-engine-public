"""
dual_pipeline/ptc/contracts.py

Core programmatic schemas, contracts, and error definitions for the
Governed Programmatic Tool Calling (PTC) subsystem.
Layer: L0 (Contracts & Primitives).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


class PTCStatus(StrEnum):
    SUCCESS = "SUCCESS"
    SPOOLED_POINTER = "SPOOLED_POINTER"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"


class PTCError(Exception):
    """Base exception for all Programmatic Tool Calling errors."""


class PTCSecurityViolationError(PTCError):
    """Raised when an AST inspection detects forbidden imports or system operations."""


class PTCTimeoutError(PTCError):
    """Raised when script execution exceeds the declared timeout budget."""


class PTCExecutionError(PTCError):
    """Raised when script execution fails at runtime."""


@dataclass(frozen=True)
class TokenBudget:
    """Enforceable payload ceiling and budget for programmatic tool results."""
    max_payload_bytes: int = 2048
    max_input_tokens: int = 200_000
    max_output_tokens: int = 32_000
    enforce_fail_closed: bool = True

    def is_exceeded(self, payload_bytes: int) -> bool:
        return payload_bytes > self.max_payload_bytes


@dataclass
class PTCExecutionContext:
    """Environment and security context passed into the PTC sandbox."""
    task_id: str
    runtime_root: Path
    timeout_seconds: float = 15.0
    token_budget: TokenBudget = field(default_factory=TokenBudget)
    allowed_modules: Sequence[str] = field(default_factory=lambda: [
        "math", "json", "re", "datetime", "collections", "itertools", "dataclasses", "typing"
    ])
    proxies: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PTCResultSummary:
    """Bounded, contract-valid result returned to the model or orchestrator."""
    status: PTCStatus
    task_id: str
    execution_time_ms: float
    payload_bytes: int
    data: Any = None
    artifact_ref: Optional[str] = None
    sha256_digest: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
