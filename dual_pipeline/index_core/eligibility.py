"""Deterministic retrieval eligibility classification for assertions."""
from __future__ import annotations

from typing import Any, Mapping

from dual_pipeline.l0.assumption_lifecycle import is_assertion_temporally_expired
from dual_pipeline.retrieval_clearance import retrieval_clearance  # noqa: lazy-decouple
from .constants import APPROVED_VALIDATION_STATES


def _get_field(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _classification(value: Any) -> str:
    val = str(value or "")
    if val == "external_approved":
        return "public"
    if val in {"public", "internal", "confidential", "restricted"}:
        return val
    return "internal"


def effective_retrieval_lane(row: Mapping[str, Any] | Any) -> str:
    """Return the effective retrieval lane ('approved' or 'candidate') for an assertion row."""
    canonical_status = str(_get_field(row, "canonical_status", "") or "")
    validation_state = str(_get_field(row, "validation_state", "") or "")
    canonical_eligible = _get_field(row, "canonical_eligible", 1)
    confidence_raw = _get_field(row, "confidence_score")
    confidence_score = float(confidence_raw) if confidence_raw is not None else 1.0
    if (
        canonical_status == "canonical"
        and validation_state in APPROVED_VALIDATION_STATES
        and bool(canonical_eligible)
        and confidence_score >= 0.65
    ):
        return "approved"
    return "candidate"


def is_single_source_directional(
    row: Mapping[str, Any] | Any,
    *,
    distinct_source_count: int | None = None,
    source_posture: str | None = None,
    source_type: str | None = None,
) -> bool:
    """Return True if an assertion originates from a single uncorroborated directional/copilot source."""
    posture = str(source_posture or _get_field(row, "source_posture", "") or "").lower()
    stype = str(source_type or _get_field(row, "source_type", "") or "").lower()
    if posture in {"transcript-directional", "directional", "citation_only_weekly_snapshot"} or stype == "copilot_summary":
        return True
    src_cnt = distinct_source_count if distinct_source_count is not None else _get_field(row, "corroborating_source_count")
    auth_rank = _get_field(row, "primary_source_authority_rank")
    if src_cnt is not None and int(src_cnt) <= 1:
        val_prio = str(_get_field(row, "validation_priority", "") or "").upper()
        if val_prio in {"P1", "P2"}:
            return True
        if auth_rank is not None and int(auth_rank) >= 5:
            return True
    return False


def _check_canonical_status_rules(
    canonical_status: str,
    currentness: str,
    canonical_eligible: Any,
    confidence_score: float,
    validation_state: str,
    allow_unapproved_canonical_as_candidate: bool,
) -> str | None:
    if canonical_status == "canonical":
        if currentness != "current":
            return "EXCLUDED_HISTORICAL"
        if not bool(canonical_eligible):
            return "EXCLUDED_UNAPPROVED_CANONICAL"
        if confidence_score < 0.65:
            return "EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG"
        if validation_state not in APPROVED_VALIDATION_STATES and not allow_unapproved_canonical_as_candidate:
            return "EXCLUDED_UNAPPROVED_CANONICAL"
        return None
    if canonical_status in {"candidate", "historical_candidate"}:
        return "EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG" if confidence_score < 0.65 else None
    return "EXCLUDED_STATUS_NOT_SELECTED"


def classify_retrieval_eligibility(
    row: Mapping[str, Any] | Any,
    *,
    has_supporting_evidence: bool = True,
    allow_unapproved_canonical_as_candidate: bool = False,
) -> tuple[bool, str]:
    """Classify assertion eligibility for retrieval projection under governed rules."""
    currentness = str(_get_field(row, "currentness_state", "") or "")
    if currentness not in {"current", "unknown"}:
        return False, "EXCLUDED_HISTORICAL"

    if is_assertion_temporally_expired(_get_field(row, "valid_to"), _get_field(row, "qualifiers_json")):
        return False, "EXCLUDED_HISTORICAL"

    retrieval_eligible = _get_field(row, "retrieval_eligible")
    if retrieval_eligible is not None and not bool(retrieval_eligible):
        return False, "EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG"

    canonical_status = str(_get_field(row, "canonical_status", "") or "")
    validation_state = str(_get_field(row, "validation_state", "") or "")
    confidence_raw = _get_field(row, "confidence_score")
    confidence_score = float(confidence_raw) if confidence_raw is not None else 1.0

    status_err = _check_canonical_status_rules(
        canonical_status, currentness, _get_field(row, "canonical_eligible", 1),
        confidence_score, validation_state, allow_unapproved_canonical_as_candidate,
    )
    if status_err:
        return False, status_err

    if not has_supporting_evidence:
        return False, "EXCLUDED_NO_SUPPORTING_EVIDENCE"

    if effective_retrieval_lane(row) == "approved" and validation_state not in APPROVED_VALIDATION_STATES:
        return False, "EXCLUDED_UNAPPROVED_LANE"

    allowed, reason = retrieval_clearance(
        assertion_type=_get_field(row, "assertion_type"),
        classification=_classification(_get_field(row, "classification")),
        audiences=str(_get_field(row, "allowed_audiences_json", "[]") or "[]"),
        origin_type=_get_field(row, "origin_type") or "legacy_migration",
        extraction_run_id=_get_field(row, "extraction_run_id"),
        hitl_required=_get_field(row, "hitl_required"),
        validation_priority=_get_field(row, "validation_priority"),
        validation_owner=_get_field(row, "validation_owner"),
        atomicity_state=_get_field(row, "atomicity_state"),
    )
    if not allowed:
        return False, f"EXCLUDED_CLEARANCE_{str(reason or 'unknown').strip().upper()}"

    return True, "ELIGIBLE"

