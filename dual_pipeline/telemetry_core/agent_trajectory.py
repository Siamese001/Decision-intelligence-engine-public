"""RFC-8259 compliant agent trajectory telemetry recorder and privacy guard.

Captures agentic execution efficiency metrics:
- Tool turns to convergence (step count)
- Tool call latency tracking
- Tool error rate
- Edit churn count and edit flail index
- Convergence status lifecycle
Enforces strict telemetry privacy invariants (no raw prompts, secrets, or confidential text).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

PROHIBITED_METADATA_KEYS: Set[str] = {
    "prompt",
    "raw_prompt",
    "user_prompt",
    "system_prompt",
    "response_text",
    "raw_evidence",
    "transcript",
    "raw_transcript",
    "secret",
    "api_key",
    "password",
    "token",
    "auth",
}

SECRET_PATTERNS = (
    re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"\b(ghp_[A-Za-z0-9]{36})\b"),
)


class TelemetryPrivacyViolation(ValueError):
    """Raised when an outbound telemetry payload violates sensitive data restrictions."""


def utc_iso_now() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def scrub_sensitive_string(val: str) -> str:
    """Redact known secret patterns from string values."""
    sanitized = val
    for pat in SECRET_PATTERNS:
        sanitized = pat.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


def validate_and_sanitize_metadata(
    data: Dict[str, Any],
    strict: bool = True,
) -> Dict[str, Any]:
    """Recursively validate that no prohibited keys or secrets exist in telemetry metadata."""
    sanitized: Dict[str, Any] = {}
    for k, v in data.items():
        k_lower = k.lower().strip()
        if k_lower in PROHIBITED_METADATA_KEYS:
            if strict:
                raise TelemetryPrivacyViolation(
                    f"PROHIBITED_TELEMETRY_KEY: Metadata key '{k}' violates telemetry privacy governance."
                )
            continue

        if isinstance(v, dict):
            sanitized[k] = validate_and_sanitize_metadata(v, strict=strict)
        elif isinstance(v, str):
            sanitized[k] = scrub_sensitive_string(v)
        elif isinstance(v, (list, tuple, set)):
            sanitized[k] = [
                scrub_sensitive_string(item) if isinstance(item, str) else item
                for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


@dataclass
class TrajectoryStep:
    step_index: int
    tool_name: str
    latency_ms: float
    status: str = "SUCCESS"  # SUCCESS, ERROR, TIMEOUT, CANCELLED
    files_modified: List[str] = field(default_factory=list)
    error_type: Optional[str] = None
    timestamp_utc: str = field(default_factory=utc_iso_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentTrajectoryTelemetry:
    """Manages an agent session trajectory lifecycle, calculating convergence metrics."""

    VALID_CONVERGENCE_STATUSES = {
        "IN_PROGRESS",
        "CONVERGED",
        "FAILED",
        "QUARANTINED",
        "ESCALATED",
        "BLOCKED",
    }

    def __init__(
        self,
        agent_session_id: str,
        agent_role: str = "assistant",
        strict_privacy: bool = True,
    ) -> None:
        self.agent_session_id = agent_session_id
        self.agent_role = agent_role
        self.strict_privacy = strict_privacy
        self.started_at_utc: str = utc_iso_now()
        self.ended_at_utc: Optional[str] = None
        self.convergence_status: str = "IN_PROGRESS"
        self.steps: List[TrajectoryStep] = []
        self._edited_files_history: List[str] = []
        self.total_lines_added: int = 0
        self.total_lines_deleted: int = 0
        self.metadata: Dict[str, Any] = {}

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.steps)

    @property
    def avg_latency_ms(self) -> float:
        return (self.total_latency_ms / len(self.steps)) if self.steps else 0.0

    @property
    def tool_errors(self) -> int:
        return sum(1 for s in self.steps if s.status != "SUCCESS")

    @property
    def tool_error_rate(self) -> float:
        return round(self.tool_errors / len(self.steps), 4) if self.steps else 0.0

    @property
    def edit_churn_count(self) -> int:
        return len(self._edited_files_history)

    @property
    def edit_flail_index(self) -> float:
        """Measure repetitive file editing (thrashing).

        0.0 = every edit was on a different file (smooth progression).
        Approaching 1.0 = same small set of files edited repeatedly.
        """
        total = len(self._edited_files_history)
        if total <= 1:
            return 0.0
        unique = len(set(self._edited_files_history))
        return round(max(0.0, (total - unique) / total), 4)

    def record_step(
        self,
        tool_name: str,
        latency_ms: float,
        status: str = "SUCCESS",
        files_modified: Optional[Sequence[str]] = None,
        error_type: Optional[str] = None,
    ) -> TrajectoryStep:
        """Record a single tool call step in the agent trajectory."""
        files = list(files_modified or [])
        step = TrajectoryStep(
            step_index=len(self.steps) + 1,
            tool_name=tool_name,
            latency_ms=round(latency_ms, 2),
            status=status.upper(),
            files_modified=files,
            error_type=error_type,
            timestamp_utc=utc_iso_now(),
        )
        self.steps.append(step)
        for f in files:
            self._edited_files_history.append(f)
        return step

    def record_edit(
        self,
        file_path: str,
        lines_added: int = 0,
        lines_deleted: int = 0,
    ) -> None:
        """Record a file modification and update churn counters."""
        self._edited_files_history.append(file_path)
        self.total_lines_added += max(0, lines_added)
        self.total_lines_deleted += max(0, lines_deleted)

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        """Set sanitized session metadata enforcing privacy invariants."""
        self.metadata = validate_and_sanitize_metadata(metadata, strict=self.strict_privacy)

    def mark_convergence(self, status: str = "CONVERGED") -> None:
        """Finalize the agent session and record terminal timestamp."""
        status_upper = status.upper()
        if status_upper not in self.VALID_CONVERGENCE_STATUSES:
            raise ValueError(
                f"Invalid convergence status '{status}'. Must be one of: {sorted(self.VALID_CONVERGENCE_STATUSES)}"
            )
        self.convergence_status = status_upper
        self.ended_at_utc = utc_iso_now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert the trajectory record into a structured dictionary."""
        return {
            "contract_id": "urn:nee:dual-pipeline:agent-trajectory-telemetry:1.0.0",
            "agent_session_id": self.agent_session_id,
            "agent_role": self.agent_role,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "convergence_status": self.convergence_status,
            "metrics": {
                "step_count": self.step_count,
                "total_latency_ms": round(self.total_latency_ms, 2),
                "avg_latency_ms": round(self.avg_latency_ms, 2),
                "tool_errors": self.tool_errors,
                "tool_error_rate": self.tool_error_rate,
                "edit_churn_count": self.edit_churn_count,
                "edit_flail_index": self.edit_flail_index,
                "total_lines_added": self.total_lines_added,
                "total_lines_deleted": self.total_lines_deleted,
            },
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
        }

    def to_rfc8259_canonical_json(self) -> str:
        """Serialize payload to RFC-8259 canonical JSON (sorted keys, compact separators)."""
        data = self.to_dict()
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def canonical_digest(self) -> str:
        """Calculate the SHA-256 digest of the canonical JSON representation."""
        return hashlib.sha256(self.to_rfc8259_canonical_json().encode("utf-8")).hexdigest()
