"""Dataclasses, protocols, and error types for L2 providers and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from dual_pipeline.core import digest
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Disposition,
    L2ExecutionDecision,
    ModelPolicy,
    ProviderFailureCode,
    QualityFailureCode,
    SchemaFailureCode,
)
from dual_pipeline.provider_request import aggregate_token_usage


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_reported: bool = False


@dataclass(frozen=True)
class L2ProviderRequest:
    purpose: CallPurpose
    question: str
    plan: Mapping[str, Any]
    evidence_packet: Mapping[str, Any] | None
    prompt: Mapping[str, Any] | None
    original_response: Any | None = None
    validation_errors: tuple[str, ...] = ()
    quality_deficiencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class L2ProviderResponse:
    output: Any
    response_digest: str
    model: str
    usage: TokenUsage
    latency_ms: float


class ReasoningProvider(Protocol):
    provider_id: str
    model_policy: ModelPolicy

    def generate(self, request: L2ProviderRequest) -> L2ProviderResponse: ...


class ReasoningProviderFactory(Protocol):
    def __call__(self, model_policy: ModelPolicy) -> ReasoningProvider: ...


class L2ProviderError(RuntimeError):
    """A provider failure carrying only a typed code and sanitized message."""

    def __init__(
        self,
        code: ProviderFailureCode,
        safe_message: str,
        *,
        usage: TokenUsage | None = None,
        response_digest: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        self.code = code
        self.safe_message = safe_message
        self.usage = usage
        self.response_digest = response_digest
        self.latency_ms = latency_ms
        super().__init__(safe_message)


@dataclass(frozen=True)
class L2CallRecord:
    provider: str
    model: str
    model_profile: str
    reasoning_effort: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_input_cost: float
    estimated_output_cost: float
    estimated_total_cost: float
    latency_ms: float
    call_purpose: str
    call_ordinal: int
    call_cap_status: str
    response_digest: str | None
    status: str
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class SchemaIssue:
    code: SchemaFailureCode
    path: str
    message: str
    repairable: bool

    def to_text(self) -> str:
        return f"{self.code.value} at {self.path}: {self.message}"


@dataclass(frozen=True)
class SchemaValidationResult:
    value: Mapping[str, Any] | None
    issues: tuple[SchemaIssue, ...]

    @property
    def valid(self) -> bool:
        return self.value is not None and not self.issues

    @property
    def repairable(self) -> bool:
        return bool(self.issues) and all(issue.repairable for issue in self.issues)


@dataclass(frozen=True)
class QualityDeficiency:
    code: QualityFailureCode
    field: str
    detail: str
    required_record_ids: tuple[str, ...] = ()
    missing_record_ids: tuple[str, ...] = ()

    def to_text(self) -> str:
        return f"{self.code.value} at {self.field}: {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "field": self.field,
            "detail": self.detail,
            "required_record_ids": list(self.required_record_ids),
            "missing_record_ids": list(self.missing_record_ids),
        }


@dataclass
class _CallBudget:
    decision: L2ExecutionDecision
    primary: int = 0
    repair: int = 0
    escalation: int = 0

    def consume(self, purpose: CallPurpose) -> int:
        if purpose in (
            CallPurpose.PRIMARY,
            CallPurpose.FORENSIC_TRANSCRIPT,
            CallPurpose.EXECUTIVE_RECAP,
            CallPurpose.KG_SIGNAL_PROJECTION,
            CallPurpose.AGENTIC_LEVERAGE,
        ):
            if self.primary >= self.decision.maximum_primary_calls:
                raise L2ProviderError(
                    ProviderFailureCode.CALL_CAP_EXHAUSTED,
                    "primary external-call cap is exhausted",
                )
            self.primary += 1
        elif purpose is CallPurpose.SCHEMA_REPAIR:
            if self.repair >= self.decision.maximum_repair_calls:
                raise L2ProviderError(
                    ProviderFailureCode.CALL_CAP_EXHAUSTED,
                    "schema-repair external-call cap is exhausted",
                )
            self.repair += 1
        else:
            if self.escalation >= self.decision.maximum_escalation_calls:
                raise L2ProviderError(
                    ProviderFailureCode.CALL_CAP_EXHAUSTED,
                    "quality-escalation external-call cap is exhausted",
                )
            self.escalation += 1
        return self.primary + self.repair + self.escalation


@dataclass(frozen=True)
class L2ExecutionResult:
    disposition: L2Disposition
    answer: Mapping[str, Any] | None
    selected_output_digest: str | None
    call_records: tuple[L2CallRecord, ...]
    provider_initialization_count: int
    delivered_model: str | None = None
    delivered_model_profile: str | None = None
    schema_issues: tuple[SchemaIssue, ...] = ()
    quality_deficiencies: tuple[QualityDeficiency, ...] = ()
    provider_failure_code: str | None = None
    provider_failure: str | None = None
    judge_call_count: int = 0
    challenger_call_count: int = 0

    @property
    def model_call_count(self) -> int:
        return len(self.call_records)

    @property
    def provider_call_count(self) -> int:
        return len(self.call_records)

    @property
    def primary_call_count(self) -> int:
        return sum(record.call_purpose == CallPurpose.PRIMARY.value for record in self.call_records)

    @property
    def schema_repair_call_count(self) -> int:
        return sum(
            record.call_purpose == CallPurpose.SCHEMA_REPAIR.value for record in self.call_records
        )

    @property
    def quality_escalation_call_count(self) -> int:
        return sum(
            record.call_purpose == CallPurpose.QUALITY_ESCALATION.value
            for record in self.call_records
        )

    def to_ledger_payload(self) -> dict[str, Any]:
        usage = aggregate_token_usage(self.call_records)
        return {
            "l2_disposition": self.disposition.value,
            "selected_output_digest": self.selected_output_digest,
            "provider_initialization_count": self.provider_initialization_count,
            "delivered_model": self.delivered_model,
            "delivered_model_profile": self.delivered_model_profile,
            "model_call_count": self.model_call_count,
            "primary_call_count": self.primary_call_count,
            "schema_repair_call_count": self.schema_repair_call_count,
            "quality_escalation_call_count": self.quality_escalation_call_count,
            "judge_call_count": self.judge_call_count,
            "challenger_call_count": self.challenger_call_count,
            "provider_failure_code": self.provider_failure_code,
            "provider_failure": self.provider_failure,
            "schema_issues": [issue.to_text() for issue in self.schema_issues],
            "quality_deficiencies": [
                deficiency.to_text() for deficiency in self.quality_deficiencies
            ],
            "quality_deficiency_details": [
                deficiency.to_dict() for deficiency in self.quality_deficiencies
            ],
            "calls": [record.to_dict() for record in self.call_records],
            **usage,
            "estimated_input_cost": round(
                sum(record.estimated_input_cost for record in self.call_records), 12
            ),
            "estimated_output_cost": round(
                sum(record.estimated_output_cost for record in self.call_records), 12
            ),
            "estimated_total_cost": round(
                sum(record.estimated_total_cost for record in self.call_records), 12
            ),
            "latency_ms": round(sum(record.latency_ms for record in self.call_records), 3),
        }


@dataclass(frozen=True)
class ContentRepairResult:
    """One optional, bounded revision of a most-complex L2 answer."""

    answer: Mapping[str, Any] | None
    call_record: L2CallRecord | None
    provider_initialization_count: int
    provider_failure_code: str | None = None
    provider_failure: str | None = None
    skip_reason: str | None = None

    @property
    def call_count(self) -> int:
        return 1 if self.call_record is not None else 0

    @property
    def succeeded(self) -> bool:
        return self.answer is not None and self.provider_failure_code is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.succeeded else "SKIPPED" if self.skip_reason else "FAIL",
            "answer_digest": digest(self.answer) if self.answer is not None else None,
            "call_count": self.call_count,
            "call": self.call_record.to_dict() if self.call_record is not None else None,
            "provider_initialization_count": self.provider_initialization_count,
            "provider_failure_code": self.provider_failure_code,
            "provider_failure": self.provider_failure,
            "skip_reason": self.skip_reason,
        }
