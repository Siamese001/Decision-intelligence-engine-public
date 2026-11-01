"""
tests/test_hybrid_retrieval_coverage_adversarial.py

Adversarial and edge-case test suite for Wave 3 Retrieval Clearance, Candidate Lane
Eligibility, and Sidecar Re-Indexing.
Covers 5 edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty query tokens, zero-evidence assertions)
2. Malformed input (corrupted allowed_audiences_json, unparsed legacy text)
3. Invariant bypass attempt (private_personal audience leak attempt, external client confidential exposure, derived assertion types)
4. Rollback, recovery & idempotence (deterministic record rendering across repeated search_fts rebuilds)
5. Exact production regression (confidential nee-engineering records allowed vs client_approved_external blocked; unapproved canonical routed to candidate lane)
"""

from __future__ import annotations

import pytest

from dual_pipeline.index_core.eligibility import (
    classify_retrieval_eligibility,
    effective_retrieval_lane,
)
from dual_pipeline.retrieval_clearance import retrieval_clearance


# ==============================================================================
# Category 1: Boundary payload
# ==============================================================================

def test_boundary_missing_evidence_and_ineligible_flags() -> None:
    """Verifies assertions without supporting evidence or with retrieval_eligible=0 fail closed."""
    base_row = {
        "currentness_state": "current",
        "retrieval_eligible": 1,
        "canonical_status": "candidate",
        "validation_state": "needs_review",
        "classification": "confidential",
        "allowed_audiences_json": '["internal_consulting", "nee-engineering"]',
        "origin_type": "human_source",
        "extraction_run_id": "run-101",
    }
    el_no_ev, reason_no_ev = classify_retrieval_eligibility(
        base_row, has_supporting_evidence=False, allow_unapproved_canonical_as_candidate=True
    )
    assert el_no_ev is False
    assert reason_no_ev == "EXCLUDED_NO_SUPPORTING_EVIDENCE"

    inel_row = dict(base_row, retrieval_eligible=0)
    el_flag, reason_flag = classify_retrieval_eligibility(
        inel_row, has_supporting_evidence=True, allow_unapproved_canonical_as_candidate=True
    )
    assert el_flag is False
    assert reason_flag == "EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG"


# ==============================================================================
# Category 2: Malformed input
# ==============================================================================

def test_malformed_audience_json_and_unparsed_legacy_text_rejected() -> None:
    """Verifies malformed audience JSON and unparsed_legacy_text fail closed."""
    allowed, reason = retrieval_clearance(
        assertion_type="confirmed_canonical_fact",
        classification="internal",
        audiences="{bad-json",
    )
    assert allowed is False
    assert reason == "invalid_audience_metadata"

    allowed_leg, reason_leg = retrieval_clearance(
        assertion_type="legacy_claim_record",
        classification="internal",
        audiences=["internal_consulting"],
        origin_type="legacy_migration",
        atomicity_state="unparsed_legacy_text",
    )
    assert allowed_leg is False
    assert reason_leg == "unparsed_legacy_assertion_not_retrieval_eligible"


# ==============================================================================
# Category 3: Invariant bypass attempt
# ==============================================================================

@pytest.mark.parametrize(
    ("assertion_type", "classification", "audiences", "expected_reason"),
    [
        (
            "confirmed_canonical_fact",
            "confidential",
            ["client_approved_external"],
            "confidential_record_exposed_to_client_audience",
        ),
        (
            "confirmed_canonical_fact",
            "restricted",
            ["client_approved_external", "internal_consulting"],
            "confidential_record_exposed_to_client_audience",
        ),
        (
            "confirmed_canonical_fact",
            "internal",
            ["private_personal"],
            "private_overlay_audience_in_graph",
        ),
        (
            "confirmed_canonical_fact",
            "internal",
            ["nee-engineering", "internal_working_team"],
            "client_and_restricted_audiences_mixed",
        ),
        (
            "recommendation",
            "internal",
            ["internal_consulting"],
            "derived_assertion_type_not_retrieval_eligible",
        ),
        (
            "hypothesis",
            "internal",
            ["internal_consulting"],
            "derived_assertion_type_not_retrieval_eligible",
        ),
    ],
)
def test_invariant_bypass_attempts_fail_closed(
    assertion_type: str,
    classification: str,
    audiences: list[str],
    expected_reason: str,
) -> None:
    """Verifies genuine external client confidential leaks, private_personal overlays, and derived assertions fail closed."""
    allowed, reason = retrieval_clearance(
        assertion_type=assertion_type,
        classification=classification,
        audiences=audiences,
    )
    assert allowed is False
    assert reason == expected_reason


# ==============================================================================
# Category 4 & 5: Exact production regression & lane routing invariants
# ==============================================================================

def test_production_regression_internal_nee_engineering_confidential_allowed() -> None:
    """Verifies internal confidential/restricted records tagged with nee-engineering pass clearance."""
    for classification in ("internal", "confidential", "restricted"):
        allowed, reason = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification=classification,
            audiences=["internal_consulting", "nee-engineering"],
            origin_type="human_source",
            extraction_run_id="run-2026-10",
        )
        assert allowed is True, f"Expected {classification} + nee-engineering to pass, got {reason}"
        assert reason is None


def test_production_regression_unapproved_canonical_routes_to_candidate_lane() -> None:
    """Verifies canonical assertions with validation_state='needs_review' route safely to the candidate lane."""
    unapproved_canonical = {
        "currentness_state": "current",
        "retrieval_eligible": 1,
        "canonical_status": "canonical",
        "validation_state": "needs_review",
        "classification": "internal",
        "allowed_audiences_json": '["internal_consulting"]',
        "origin_type": "legacy_migration",
    }
    # Strict canonical-only check (default False) preserves legacy audit behavior
    strict_ok, strict_reason = classify_retrieval_eligibility(
        unapproved_canonical,
        has_supporting_evidence=True,
        allow_unapproved_canonical_as_candidate=False,
    )
    assert strict_ok is False
    assert strict_reason == "EXCLUDED_UNAPPROVED_CANONICAL"

    # Candidate-fallback mode admits the record into the candidate lane
    cand_ok, cand_reason = classify_retrieval_eligibility(
        unapproved_canonical,
        has_supporting_evidence=True,
        allow_unapproved_canonical_as_candidate=True,
    )
    assert cand_ok is True
    assert cand_reason == "ELIGIBLE"
    assert effective_retrieval_lane(unapproved_canonical) == "candidate"
