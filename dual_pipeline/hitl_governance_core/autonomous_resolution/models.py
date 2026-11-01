"""
dual_pipeline/hitl_governance_core/autonomous_resolution/models.py

Domain models and contracts for governed autonomous decision resolution.
Provides immutable typed contracts for decision requests, policy snapshots,
and pure eligibility results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class ResolutionTerminalResult(str, Enum):
    """Governed terminal results for decision resolution attempts."""
    RESOLVED_AUTONOMOUSLY = "RESOLVED_AUTONOMOUSLY"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    INVALID_INPUT = "INVALID_INPUT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    STORAGE_ERROR = "STORAGE_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    UNSUPPORTED_HOST = "UNSUPPORTED_HOST"


class AuthorityType(str, Enum):
    """Authority domain for decision resolution."""
    AUTONOMOUS = "AUTONOMOUS"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class DecisionMode(str, Enum):
    """Operational mode of the decision request."""
    ROUTINE = "ROUTINE"
    TACTICAL = "TACTICAL"
    ARCHITECTURAL = "ARCHITECTURAL"
    DESTRUCTIVE = "DESTRUCTIVE"
    POLICY = "POLICY"
    GOVERNANCE = "GOVERNANCE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DecisionOption:
    """A discrete substantive option evaluated within a decision request."""
    option_id: str
    description: str
    confidence: float
    action: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "option_id": self.option_id,
            "description": self.description,
            "confidence": self.confidence,
            "action": self.action,
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionOption:
        return cls(
            option_id=str(data.get("option_id", "")),
            description=str(data.get("description", data.get("label", data.get("text", "")))),
            confidence=data.get("confidence", data.get("score", 0.0)),
            action=data.get("action"),
            evidence_refs=list(data.get("evidence_refs", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class DecisionScope:
    """Execution scope and runtime bounds for a decision request."""
    worktree: str
    run_id: str
    wave: int
    action_target: str
    plan_digest: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worktree": self.worktree,
            "run_id": self.run_id,
            "wave": self.wave,
            "action_target": self.action_target,
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionScope:
        return cls(
            worktree=str(data.get("worktree", "")),
            run_id=str(data.get("run_id", "")),
            wave=int(data.get("wave", 0)),
            action_target=str(data.get("action_target", "")),
            plan_digest=data.get("plan_digest"),
        )


@dataclass(frozen=True)
class DecisionRequest:
    """Strongly typed input request for autonomous decision eligibility."""
    decision_id: str
    revision: int
    scope: DecisionScope
    options: List[DecisionOption]
    authority_type: AuthorityType = AuthorityType.AUTONOMOUS
    mode: DecisionMode = DecisionMode.ROUTINE
    category: str = "routine"
    action_description: str = ""
    target_files: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    decision_content_digest: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "revision": self.revision,
            "scope": self.scope.to_dict(),
            "options": [opt.to_dict() for opt in self.options],
            "authority_type": self.authority_type.value,
            "mode": self.mode.value,
            "category": self.category,
            "action_description": self.action_description,
            "target_files": list(self.target_files),
            "context": dict(self.context),
            "decision_content_digest": self.decision_content_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionRequest:
        auth_raw = str(data.get("authority_type", AuthorityType.AUTONOMOUS.value)).upper()
        auth_type = (
            AuthorityType(auth_raw)
            if auth_raw in AuthorityType._value2member_map_
            else AuthorityType.UNKNOWN
        )
        mode_raw = str(data.get("mode", DecisionMode.ROUTINE.value)).upper()
        mode_val = (
            DecisionMode(mode_raw)
            if mode_raw in DecisionMode._value2member_map_
            else DecisionMode.UNKNOWN
        )
        raw_scope = data.get("scope", {})
        scope = (
            raw_scope
            if isinstance(raw_scope, DecisionScope)
            else DecisionScope.from_dict(raw_scope)
        )
        options = [
            opt if isinstance(opt, DecisionOption) else DecisionOption.from_dict(opt)
            for opt in data.get("options", [])
        ]
        return cls(
            decision_id=str(data.get("decision_id", "")),
            revision=int(data.get("revision", 1)),
            scope=scope,
            options=options,
            authority_type=auth_type,
            mode=mode_val,
            category=str(data.get("category", "routine")),
            action_description=str(data.get("action_description", "")),
            target_files=list(data.get("target_files", [])),
            context=dict(data.get("context", {})),
            decision_content_digest=data.get("decision_content_digest"),
        )


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable snapshot of governing decision resolution policies."""
    snapshot_id: str
    ambiguity_threshold: float
    conviction_floor: float
    max_entropy: float
    divergence_threshold: float
    policy_source: str
    policy_digest: str
    frozen_at_iso: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "ambiguity_threshold": self.ambiguity_threshold,
            "conviction_floor": self.conviction_floor,
            "max_entropy": self.max_entropy,
            "divergence_threshold": self.divergence_threshold,
            "policy_source": self.policy_source,
            "policy_digest": self.policy_digest,
            "frozen_at_iso": self.frozen_at_iso,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolicySnapshot:
        return cls(
            snapshot_id=str(data.get("snapshot_id", "")),
            ambiguity_threshold=float(data.get("ambiguity_threshold", 0.30)),
            conviction_floor=float(data.get("conviction_floor", 0.65)),
            max_entropy=float(data.get("max_entropy", 0.75)),
            divergence_threshold=float(data.get("divergence_threshold", 0.20)),
            policy_source=str(data.get("policy_source", "unknown")),
            policy_digest=str(data.get("policy_digest", "")),
            frozen_at_iso=str(data.get("frozen_at_iso", "")),
        )


@dataclass(frozen=True)
class EligibilityEvaluationResult:
    """Immutable result of pure deterministic eligibility evaluation."""
    terminal_result: ResolutionTerminalResult
    is_eligible: bool
    selected_option_id: Optional[str]
    selected_option: Optional[DecisionOption]
    margin: float
    entropy: float
    top_confidence: float
    second_confidence: float
    reason_codes: List[str]
    reasons: List[str]
    policy_snapshot: PolicySnapshot
    evaluated_at_iso: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "terminal_result": self.terminal_result.value,
            "is_eligible": self.is_eligible,
            "selected_option_id": self.selected_option_id,
            "selected_option": self.selected_option.to_dict() if self.selected_option else None,
            "margin": self.margin,
            "entropy": self.entropy,
            "top_confidence": self.top_confidence,
            "second_confidence": self.second_confidence,
            "reason_codes": list(self.reason_codes),
            "reasons": list(self.reasons),
            "policy_snapshot": self.policy_snapshot.to_dict(),
            "evaluated_at_iso": self.evaluated_at_iso,
        }


@dataclass(frozen=True)
class AutonomousResolutionRecord:
    """Immutable persisted record of an authenticated autonomous resolution."""
    resolution_id: str
    sequence_number: int
    decision_id: str
    revision: int
    scope: DecisionScope
    authority_type: AuthorityType
    category: str
    idempotency_key: str
    selected_option_id: str
    selected_option: DecisionOption
    options: List[DecisionOption]
    margin: float
    entropy: float
    top_confidence: float
    second_confidence: float
    policy_snapshot: PolicySnapshot
    decision_content_digest: str
    previous_resolution_hash: str
    recorded_at: str
    signer_id: str
    signature_b64: str
    resolution_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "sequence_number": self.sequence_number,
            "decision_id": self.decision_id,
            "revision": self.revision,
            "scope": self.scope.to_dict(),
            "authority_type": self.authority_type.value,
            "category": self.category,
            "idempotency_key": self.idempotency_key,
            "selected_option_id": self.selected_option_id,
            "selected_option": self.selected_option.to_dict(),
            "options": [opt.to_dict() for opt in self.options],
            "margin": self.margin,
            "entropy": self.entropy,
            "top_confidence": self.top_confidence,
            "second_confidence": self.second_confidence,
            "policy_snapshot": self.policy_snapshot.to_dict(),
            "decision_content_digest": self.decision_content_digest,
            "previous_resolution_hash": self.previous_resolution_hash,
            "recorded_at": self.recorded_at,
            "signer_id": self.signer_id,
            "signature_b64": self.signature_b64,
            "resolution_digest": self.resolution_digest,
        }

    def canonical_signed_payload(self) -> Dict[str, Any]:
        """Returns the canonical dictionary payload verified by the signature."""
        return {
            "decision_content_digest": self.decision_content_digest,
            "decision_id": self.decision_id,
            "entropy": round(float(self.entropy), 4),
            "margin": round(float(self.margin), 4),
            "policy_digest": self.policy_snapshot.policy_digest,
            "previous_resolution_hash": self.previous_resolution_hash,
            "recorded_at": self.recorded_at,
            "revision": self.revision,
            "scope": self.scope.to_dict(),
            "second_confidence": round(float(self.second_confidence), 4),
            "selected_option_id": self.selected_option_id,
            "sequence_number": self.sequence_number,
            "signer_id": self.signer_id,
            "top_confidence": round(float(self.top_confidence), 4),
        }
