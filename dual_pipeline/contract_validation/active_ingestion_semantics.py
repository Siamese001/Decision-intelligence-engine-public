"""
Active ingestion contract semantics validation.
Decomposed from semantics.py to observe file budget ratchet and complexity bounds.
"""
from __future__ import annotations

from typing import Any, Callable


def _validate_run_semantics(artifact: dict[str, Any], fail: Callable[[str, str, str], None]) -> None:
    status = artifact.get("status")
    if status in {"COMPLETE_UPDATED", "COMPLETE_ALREADY_PRESENT"}:
        if artifact.get("publication_attempt_id") is None or not artifact.get("receipt_refs"):
            fail(
                "SEMANTIC_ACTIVE_INGESTION_RUN_INCOMPLETE",
                "$.status",
                "complete active ingestion requires publication attempt and receipt refs",
            )
    if status == "AWAITING_APPROVAL" and artifact.get("approval_request_id") is None:
        fail(
            "SEMANTIC_ACTIVE_INGESTION_RUN_APPROVAL_ID",
            "$.approval_request_id",
            "AWAITING_APPROVAL requires approval_request_id",
        )
    if status == "AUTHORIZED" and artifact.get("approval_response_id") is None:
        fail(
            "SEMANTIC_ACTIVE_INGESTION_RUN_RESPONSE_ID",
            "$.approval_response_id",
            "AUTHORIZED requires approval_response_id",
        )


def _validate_approval_request_semantics(artifact: dict[str, Any], fail: Callable[[str, str, str], None]) -> None:
    if artifact.get("permitted_operation_class") not in {"candidate_publication", "canonical_promotion"}:
        fail(
            "SEMANTIC_ACTIVE_INGESTION_APPROVAL_SCOPE",
            "$.permitted_operation_class",
            "invalid permitted operation class",
        )
    if artifact.get("response_status") == "APPROVED" and artifact.get("authentic_response") is None:
        fail(
            "SEMANTIC_ACTIVE_INGESTION_APPROVAL_RESPONSE",
            "$.authentic_response",
            "APPROVED requires authentic_response",
        )


def _validate_completion_receipt_semantics(artifact: dict[str, Any], fail: Callable[[str, str, str], None]) -> None:
    if artifact.get("source_coverage_verified") is not True:
        fail(
            "SEMANTIC_ACTIVE_INGESTION_NO_COVERAGE",
            "$.source_coverage_verified",
            "active ingestion completion requires verified source coverage",
        )
    if not artifact.get("readback_evidence_ids"):
        fail(
            "SEMANTIC_ACTIVE_INGESTION_NO_READBACK",
            "$.readback_evidence_ids",
            "active ingestion completion requires readback evidence",
        )


def validate_active_ingestion_semantics(
    contract_id: str,
    artifact: dict[str, Any],
    fail: Callable[[str, str, str], None],
) -> bool:
    """Validates semantic constraints for active ingestion schemas."""
    if contract_id.endswith(":active-ingestion-run:1.0.0"):
        _validate_run_semantics(artifact, fail)
        return True
    if contract_id.endswith(":active-ingestion-approval-request:1.0.0"):
        _validate_approval_request_semantics(artifact, fail)
        return True
    if contract_id.endswith(":active-ingestion-completion-receipt:1.0.0"):
        _validate_completion_receipt_semantics(artifact, fail)
        return True
    return False
