"""Data models, enums, protocols, and errors for X1 quality controls."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from ..core import ROOT, digest
from ..exceptions import DualPipelineRuntimeError

X1_QUALITY_POLICY_PATH = ROOT / "requirements" / "x1-quality-policy.json"
QUALITY_ASSURANCE_PROFILES = frozenset({"standard", "enhanced"})


class X1QualityMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCED = "enforced"


class QualityRole(StrEnum):
    CHALLENGER = "challenger"
    JUDGE = "judge"


class QualityVerdict(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    REPAIRABLE_FAIL = "REPAIRABLE_FAIL"
    ESCALATE_HITL = "ESCALATE_HITL"
    ABSTAIN = "ABSTAIN"


class QualityReviewStatus(StrEnum):
    SKIPPED = "SKIPPED"
    PASS = "PASS"
    FAIL = "FAIL"


class QualityFailureCode(StrEnum):
    PROVIDER_EGRESS_NOT_AUTHORIZED = "PROVIDER_EGRESS_NOT_AUTHORIZED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_STRUCTURED_RESPONSE = "INVALID_STRUCTURED_RESPONSE"
    REQUEST_BUDGET_EXHAUSTED = "REQUEST_BUDGET_EXHAUSTED"
    CALL_CAP_EXHAUSTED = "CALL_CAP_EXHAUSTED"
    EVIDENCE_BINDING_INVALID = "EVIDENCE_BINDING_INVALID"


QUALITY_DEFECT_CODES = frozenset(
    {
        "EVIDENCE_GROUNDING_GAP",
        "MATERIAL_QUESTION_UNANSWERED",
        "MATERIAL_COUNTEREVIDENCE_OMITTED",
        "RECOMMENDATION_RATIONALE_INCOMPLETE",
        "UNDEFINED_CLIENT_TERM",
        "CLIENT_JARGON",
        "CLIENT_READINESS_GAP",
        "CANDIDATE_POSTURE_UNCLEAR",
        "VISUAL_REVIEW_REQUIRED",
    }
)


class QualityProviderError(DualPipelineRuntimeError):
    """Typed failure whose message is safe to retain in governed receipts."""

    def __init__(self, code: QualityFailureCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True)
class QualityModelPolicy:
    provider: str
    model: str
    reasoning_effort: str
    maximum_calls_per_run: int
    maximum_output_tokens: int


@dataclass(frozen=True)
class ContentRepairPolicy:
    maximum_calls_per_run: int
    eligible_assurance_profiles: frozenset[str]


@dataclass(frozen=True)
class X1QualityConfig:
    schema_version: str
    mode: X1QualityMode
    provider_egress_allowed: bool
    endpoint_template: str
    api_key_env: str
    eligible_assurance_profiles: frozenset[str]
    maximum_request_chars: int
    challenger: QualityModelPolicy
    judge: QualityModelPolicy
    content_repair: ContentRepairPolicy
    config_digest: str

    def policy_for(self, role: QualityRole) -> QualityModelPolicy:
        return self.challenger if role is QualityRole.CHALLENGER else self.judge


@dataclass(frozen=True)
class QualityUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class QualityProviderRequest:
    role: QualityRole
    question: str
    answer: Mapping[str, Any]
    evidence_packet: Mapping[str, Any]
    deterministic_controls: Mapping[str, Any]
    challenger_review: Mapping[str, Any] | None
    input_bindings: Mapping[str, str]
    repair_attempt: int = 0


@dataclass(frozen=True)
class QualityProviderResponse:
    output: Any
    response_digest: str
    model: str
    usage: QualityUsage
    latency_ms: float


class QualityProvider(Protocol):
    provider_id: str
    model_policy: QualityModelPolicy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse: ...


class QualityProviderFactory(Protocol):
    def __call__(self, model_policy: QualityModelPolicy) -> QualityProvider: ...


@dataclass(frozen=True)
class QualityReview:
    role: QualityRole
    mode: X1QualityMode
    assurance_profile: str
    eligible: bool
    status: QualityReviewStatus
    verdict: QualityVerdict
    skip_reason: str | None
    failure_code: str | None
    provider: str | None
    model: str | None
    reasoning_effort: str | None
    input_bindings: Mapping[str, str]
    response_digest: str | None
    defect_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    repair_attempt: int = 0
    provider_call_count: int = 0

    @property
    def call_count(self) -> int:
        return self.provider_call_count

    def to_dict(self) -> dict[str, Any]:
        value = {
            "role": self.role.value,
            "mode": self.mode.value,
            "assurance_profile": self.assurance_profile,
            "eligible": self.eligible,
            "status": self.status.value,
            "verdict": self.verdict.value,
            "skip_reason": self.skip_reason,
            "failure_code": self.failure_code,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "input_bindings": dict(self.input_bindings),
            "response_digest": self.response_digest,
            "defect_codes": list(self.defect_codes),
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "repair_attempt": self.repair_attempt,
            "provider_call_count": self.provider_call_count,
        }
        value["review_digest"] = digest(value)
        return value
