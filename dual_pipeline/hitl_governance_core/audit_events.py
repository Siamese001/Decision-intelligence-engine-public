"""
dual_pipeline/hitl_governance_core/audit_events.py

Structured audit events with decision, policy, and receipt digests.
Separates resolution, delivery, operator answer, and independent outcome events
under strict credential isolation, pre-egress sanitization, and anti-fabrication invariants.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.hitl_governance_core.autonomous_resolution.signing import (
    compute_canonical_digest,
)

# Secret sanitization patterns
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[SCRUBBED_API_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[SCRUBBED_AWS_KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"), "Bearer [SCRUBBED_TOKEN]"),
    (re.compile(r"(?i)(?:password|secret|api_key|token)\s*[:=]\s*['\"]?[^\s'\"]+"), "[SCRUBBED_CREDENTIAL]"),
    (re.compile(r"(?i)\$executive_compensation_band"), "[SCRUBBED_CAREER_LEVERAGE]"),
    (re.compile(r"(?i)personal\s+advancement"), "[SCRUBBED_PERSONAL_STRATEGY]"),
]


def sanitize_audit_payload(payload: Any) -> Any:
    """Recursively scrubs sensitive secrets and career leverage markers from audit payloads."""
    if isinstance(payload, str):
        result = payload
        for pattern, replacement in SECRET_PATTERNS:
            result = pattern.sub(replacement, result)
        return result
    if isinstance(payload, dict):
        sanitized: Dict[str, Any] = {}
        for k, v in payload.items():
            k_clean = str(k).lower()
            if any(s in k_clean for s in ("password", "secret", "private_key", "bearer_token", "api_key")):
                sanitized[k] = "[SCRUBBED_CREDENTIAL]"
            else:
                sanitized[k] = sanitize_audit_payload(v)
        return sanitized
    if isinstance(payload, list):
        return [sanitize_audit_payload(item) for item in payload]
    return payload


def validate_operator_authenticity(operator_id: str, claimed_class: str) -> Tuple[bool, str]:
    """Validates that a declared operator identity is not a synthetic persona masquerading as human."""
    clean_id = str(operator_id).strip().lower()
    clean_class = str(claimed_class).strip().upper()

    is_synthetic = any(t in clean_id for t in ("auto", "autonomous", "mock", "synthetic", "test", "bot"))

    if clean_class == "HUMAN" and is_synthetic:
        return False, f"SYNTHETIC_HUMAN_FORGERY: Operator '{operator_id}' cannot be classified as HUMAN."

    return True, "AUTHENTIC"


@dataclass
class DecisionResolutionEvent:
    """Emitted when a decision is evaluated and either resolved autonomously or gated for human review."""
    decision_id: str
    revision: int
    authority_type: str
    resolution_status: str
    reason_code: str
    decision_content_digest: str
    policy_digest: str
    resolution_digest: str
    selected_option_id: Optional[str] = None
    margin: Optional[float] = None
    entropy: Optional[float] = None
    event_type: str = "decision_resolved"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuestionDeliveryEvent:
    """Emitted when an operator question is dispatched to the host or marked pending."""
    decision_id: str
    delivery_status: str
    transport: str
    decision_content_digest: str
    policy_digest: str
    reason_code: Optional[str] = None
    event_type: str = "question_delivered"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperatorAnswerEvent:
    """Emitted when a human operator answers a question."""
    decision_id: str
    operator_id: str
    approval_origin: str
    selected_option_id: str
    outcome_type: str
    decision_content_digest: str
    entry_digest: str
    event_type: str = "operator_answered"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IndependentOutcomeEvent:
    """Emitted when an independent downstream execution outcome is observed for a resolved decision."""
    decision_id: str
    selected_option_id: str
    outcome_status: str
    confidence_impact: float
    evidence_ref: str
    receipt_digest: str
    reason_code: str
    event_type: str = "independent_outcome_recorded"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def emit_audit_event(
    event: Union[
        DecisionResolutionEvent,
        QuestionDeliveryEvent,
        OperatorAnswerEvent,
        IndependentOutcomeEvent,
        Dict[str, Any],
    ],
    destination_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Sanitizes, validates, signs, and commits a structured audit event."""
    raw = event.to_dict() if hasattr(event, "to_dict") else dict(event)

    # Validate operator authenticity if present
    op_id = raw.get("operator_id")
    op_class = raw.get("authority_type") or raw.get("operator_class") or (
        "HUMAN" if raw.get("event_type") == "operator_answered" else ""
    )
    if op_id:
        is_valid, reason = validate_operator_authenticity(str(op_id), str(op_class or "HUMAN"))
        if not is_valid:
            raw["authenticity_error"] = reason
            raw["is_authentic"] = False
        else:
            raw["is_authentic"] = True

    sanitized = sanitize_audit_payload(raw)
    event_digest = compute_canonical_digest(sanitized)
    sanitized["event_digest"] = event_digest

    if destination_path is not None:
        target = Path(destination_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(sanitized, sort_keys=True) + "\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    return sanitized
