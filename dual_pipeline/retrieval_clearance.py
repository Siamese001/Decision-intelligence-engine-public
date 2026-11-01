"""Fail-closed retrieval clearance for the W1 boundary shield.

This is a query-time safety control. It does not change graph authority or
rewrite existing sidecars; it prevents known derived and unsafe-audience
assertions from being returned until their exact migration is approved.
"""

from __future__ import annotations

from typing import Iterable

from .label_quality import LabelQualityError, canonicalize_audiences

RETRIEVAL_CLEARANCE_POLICY_VERSION = "2026-10-30.1"
DERIVED_ASSERTION_TYPES = frozenset(
    {"human_interpretation", "recommendation", "hypothesis", "pov"}
)
LINEAGE_BACKED_OPERATIONAL_DERIVED_TYPES = frozenset(
    {"hypothesis", "assumption"}
)
CLIENT_AUDIENCES = frozenset({"nee-engineering", "client_approved_external"})
EXTERNAL_CLIENT_AUDIENCES = frozenset({"client_approved_external"})
RESTRICTED_AUDIENCES = frozenset(
    {"internal_working_team", "partner_advisory_account_leadership", "private_personal"}
)
GRAPH_FORBIDDEN_AUDIENCES = frozenset({"private_personal"})
CONFIDENTIAL_CLASSIFICATIONS = frozenset({"confidential", "restricted"})


def _is_incomplete_hitl_assignment(
    hitl_required: int | bool | None,
    validation_priority: str | None,
    validation_owner: str | None,
) -> bool:
    if hitl_required not in (1, True):
        return False
    return (
        not isinstance(validation_priority, str)
        or not validation_priority.strip()
        or not isinstance(validation_owner, str)
        or not validation_owner.strip()
    )


def _is_confidential_client_exposure(
    classification: str,
    normalized: set[str],
    origin_type: str | None,
    extraction_run_id: str | None,
) -> bool:
    if classification not in CONFIDENTIAL_CLASSIFICATIONS:
        return False
    if normalized & EXTERNAL_CLIENT_AUDIENCES:
        return True
    return bool(normalized & CLIENT_AUDIENCES and origin_type is None and extraction_run_id is None)


def retrieval_clearance(
    *,
    assertion_type: str | None,
    classification: str,
    audiences: Iterable[str] | str,
    origin_type: str | None = None,
    extraction_run_id: str | None = None,
    hitl_required: int | bool | None = None,
    validation_priority: str | None = None,
    validation_owner: str | None = None,
    atomicity_state: str | None = None,
) -> tuple[bool, str | None]:
    """Return whether an assertion may enter a retrieval result."""

    if assertion_type in DERIVED_ASSERTION_TYPES:
        has_extraction_lineage = (
            isinstance(extraction_run_id, str)
            and bool(extraction_run_id.strip())
            and origin_type not in (None, "legacy_migration")
        )
        if not (assertion_type in LINEAGE_BACKED_OPERATIONAL_DERIVED_TYPES and has_extraction_lineage):
            return False, "derived_assertion_type_not_retrieval_eligible"
    if atomicity_state == "unparsed_legacy_text":
        return False, "unparsed_legacy_assertion_not_retrieval_eligible"
    if origin_type not in (None, "legacy_migration") and not extraction_run_id:
        return False, "missing_nonlegacy_extraction_lineage"
    if _is_incomplete_hitl_assignment(hitl_required, validation_priority, validation_owner):
        return False, "hitl_assignment_incomplete"
    try:
        normalized = set(canonicalize_audiences(audiences))
    except (LabelQualityError, TypeError, ValueError):
        return False, "invalid_audience_metadata"
    if normalized & GRAPH_FORBIDDEN_AUDIENCES:
        return False, "private_overlay_audience_in_graph"
    if normalized & CLIENT_AUDIENCES and normalized & RESTRICTED_AUDIENCES:
        return False, "client_and_restricted_audiences_mixed"
    if _is_confidential_client_exposure(classification, normalized, origin_type, extraction_run_id):
        return False, "confidential_record_exposed_to_client_audience"
    return True, None

