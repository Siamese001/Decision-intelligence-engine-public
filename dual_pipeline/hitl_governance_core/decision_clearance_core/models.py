"""
dual_pipeline/hitl_governance_core/decision_clearance_core/models.py

Domain models and constants for unified decision clearance and pre-wave intercept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


EXIT_CODE_CLEARED = 0
EXIT_CODE_OPERATIONAL_ERROR = 1
EXIT_CODE_OPERATOR_INPUT_REQUIRED = 2
EXIT_CODE_NO_DECISIONS = 3


class ClearanceAuthority(str, Enum):
    HUMAN = "HUMAN"
    AUTONOMOUS = "AUTONOMOUS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScopedDecision:
    """A decision identified within the scope of an implementation plan or run."""

    decision_id: str
    title: str
    wave: Optional[int]
    top_confidence: float
    second_confidence: float
    handling: str  # "Ask", "Resolved autonomously", etc.
    status: str  # "OPEN", "RATIFIED", "RESOLVED"
    question: str
    options: List[Dict[str, Any]]
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    category: str = "OPERATIONAL"

    @property
    def gap(self) -> float:
        return abs(self.top_confidence - self.second_confidence)

    @property
    def requires_asking(self) -> bool:
        """Evaluates whether asking is mandatory under the Wave 4 SSOT Ambiguity Rule."""
        from dual_pipeline.parameters import (
            get_hitl_min_autonomous_confidence_floor,
            needs_operator_decision,
        )

        if "ask" in self.handling.lower():
            return True
        if self.top_confidence < get_hitl_min_autonomous_confidence_floor():
            return True
        return needs_operator_decision(self.top_confidence, self.second_confidence)


@dataclass(frozen=True)
class PreWaveDecision:
    """Represents a governed decision associated with a pipeline wave."""

    decision_id: str
    wave: int
    status: str  # "OPEN", "RATIFIED", "RESOLVED", "REJECTED"
    question: str
    options: List[Dict[str, Any]]
    category: str = "OPERATIONAL"
    plan_id: Optional[str] = None
    target_files: List[str] = field(default_factory=list)
    action_description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_open(self) -> bool:
        return self.status.upper() in ("OPEN", "UNRATIFIED", "PENDING", "UNRESOLVED")


@dataclass(frozen=True)
class ClearanceResult:
    """Outcome of evaluating operator decision clearance in scope."""

    cleared: bool
    exit_code: int
    status_code: str  # "CLEARED", "OPERATOR_INPUT_REQUIRED", "NO_DECISIONS_FOUND", "ERROR"
    open_decisions: List[ScopedDecision] = field(default_factory=list)
    resolved_decisions: List[ScopedDecision] = field(default_factory=list)
    blocked_decision: Optional[ScopedDecision] = None
    next_question_payload: Optional[Dict[str, Any]] = None
    diagnostics: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cleared": self.cleared,
            "exit_code": self.exit_code,
            "status_code": self.status_code,
            "open_count": len(self.open_decisions),
            "resolved_count": len(self.resolved_decisions),
            "open_decision_ids": [d.decision_id for d in self.open_decisions],
            "resolved_decision_ids": [d.decision_id for d in self.resolved_decisions],
            "blocked_decision_id": self.blocked_decision.decision_id if self.blocked_decision else None,
            "next_question_payload": self.next_question_payload,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class PreWaveGateResult:
    """Immutable evaluation result of the Pre-Wave Decision Gate."""

    wave: int
    clearance_granted: bool
    exit_code: int
    open_decisions: List[PreWaveDecision] = field(default_factory=list)
    resolved_decisions: List[PreWaveDecision] = field(default_factory=list)
    ask_user_question_payload: Optional[Dict[str, Any]] = None
    diagnostics: List[str] = field(default_factory=list)

    @property
    def ask_question_payload(self) -> Optional[Dict[str, Any]]:
        """Deprecated backward compatibility alias for ask_user_question_payload."""
        return self.ask_user_question_payload

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wave": self.wave,
            "clearance_granted": self.clearance_granted,
            "exit_code": self.exit_code,
            "open_decisions_count": len(self.open_decisions),
            "resolved_decisions_count": len(self.resolved_decisions),
            "open_decision_ids": [d.decision_id for d in self.open_decisions],
            "resolved_decision_ids": [d.decision_id for d in self.resolved_decisions],
            "ask_user_question_payload": self.ask_user_question_payload,
            "ask_question_payload": self.ask_user_question_payload,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class NoDecisionAttestation:
    """Explicit attestation certifying zero qualifying decisions in a wave."""

    wave: int
    reason: str
    attested_by: str = "plan_declaration"
    plan_path: Optional[str] = None


@dataclass(frozen=True)
class ClearanceVerificationRequest:
    """Typed request for exact-scope decision clearance verification."""

    decision_id: str
    wave: Optional[int] = None
    run_id: Optional[str] = None
    worktree: Optional[str] = None
    options: Optional[Sequence[Dict[str, Any]]] = None
    require_human_only: bool = False
    policy_digest: Optional[str] = None
    expected_action_target: Optional[str] = None
    enforce_rollout_switch: bool = False


@dataclass(frozen=True)
class ClearanceVerificationResult:
    """Result of exact-scope decision clearance verification."""

    cleared: bool
    authority_type: Optional[ClearanceAuthority]
    status_code: str
    reason: str
    record_id: Optional[str] = None
    digest: Optional[str] = None
    operator_id: Optional[str] = None
    selected_option_id: Optional[str] = None
