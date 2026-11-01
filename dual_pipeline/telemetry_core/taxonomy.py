"""Canonical Typed Error Taxonomy for Dual-Pipeline Observability.

Codifies the 5 canonical failure classes across all pipelines:
1. TransientNetworkError: Provider/API/network timeouts or rate limits.
2. PolicyViolationError: Data classification, boundary or safety violations.
3. SchemaValidationError: Contract, malformed JSON, or schema check errors.
4. BudgetExhaustionError: Step, token, or financial budget exhaustion.
5. AmbiguityThresholdExceeded: HITL ambiguity margin (<15%) threshold violations.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


class PipelineError(Exception):
    """Base exception for all structured pipeline errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "PIPELINE_ERROR",
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.correlation_id = correlation_id
        self.details = details or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "details": self.details,
        }


class TransientNetworkError(PipelineError):
    """Temporary network or provider failure that may succeed upon retry."""

    def __init__(
        self,
        message: str,
        correlation_id: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = details or {}
        if retry_after_seconds is not None:
            merged_details["retry_after_seconds"] = retry_after_seconds
        super().__init__(
            message=message,
            error_code="TRANSIENT_NETWORK_ERROR",
            correlation_id=correlation_id,
            details=merged_details,
        )


class PolicyViolationError(PipelineError):
    """Action rejected by data classification, career firewall, or security policy."""

    def __init__(
        self,
        message: str,
        policy_name: str = "DEFAULT_POLICY",
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = details or {}
        merged_details["policy_name"] = policy_name
        super().__init__(
            message=message,
            error_code="POLICY_VIOLATION_ERROR",
            correlation_id=correlation_id,
            details=merged_details,
        )


class SchemaValidationError(PipelineError):
    """Payload failed schema validation or structural conformance checks."""

    def __init__(
        self,
        message: str,
        schema_urn: Optional[str] = None,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = details or {}
        if schema_urn:
            merged_details["schema_urn"] = schema_urn
        super().__init__(
            message=message,
            error_code="SCHEMA_VALIDATION_ERROR",
            correlation_id=correlation_id,
            details=merged_details,
        )


class BudgetExhaustionError(PipelineError):
    """Execution halted due to token, turn, tool-call, or computational budget depletion."""

    def __init__(
        self,
        message: str,
        budget_limit: float = 0.0,
        consumed: float = 0.0,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = details or {}
        merged_details["budget_limit"] = budget_limit
        merged_details["consumed"] = consumed
        super().__init__(
            message=message,
            error_code="BUDGET_EXHAUSTION_ERROR",
            correlation_id=correlation_id,
            details=merged_details,
        )


class AmbiguityThresholdExceeded(PipelineError):
    """Decision confidence margin between top options is <25%, requiring HITL resolution."""

    def __init__(
        self,
        message: str,
        margin_delta: float = 0.0,
        candidate_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = details or {}
        merged_details["margin_delta"] = margin_delta
        if candidate_id:
            merged_details["candidate_id"] = candidate_id
        super().__init__(
            message=message,
            error_code="AMBIGUITY_THRESHOLD_EXCEEDED",
            correlation_id=correlation_id,
            details=merged_details,
        )
