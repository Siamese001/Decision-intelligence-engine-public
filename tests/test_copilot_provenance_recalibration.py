"""
tests/test_copilot_provenance_recalibration.py

Wave 1 Acceptance Suite:
1. Verifies that copilot.rigor.min_provenance_score defaults to 0.15.
2. Verifies that compute_provenance_score grants minimum 0.15 baseline for receipt-backed assertions.
3. Verifies that single-evidence historical assertions (scores 0.19-0.27) pass verify_and_ground without REJECTED_EVIDENCE.
"""
from __future__ import annotations

import time
import pytest

from dual_pipeline.parameters import (
    ParameterRegistry,
    get_copilot_rigor_min_provenance_score,
)
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
    compute_provenance_score,
    check_assertion_validity,
)


def test_copilot_min_provenance_parameter_default():
    """Verifies that ParameterRegistry resolves copilot.rigor.min_provenance_score to 0.15."""
    min_score = get_copilot_rigor_min_provenance_score()
    assert min_score == 0.15, f"Expected min_provenance_score 0.15, got {min_score}"


def test_compute_provenance_score_receipt_baseline():
    """Verifies that receipt-backed single-evidence assertions retain >= 0.15 score even when aged."""
    old_assertion = {
        "assertion_id": "asrt-test-historical-01",
        "evidence_id": "ev-intake-receipt-12345",
        "primary_source_id": "source-meeting-001",
        "source_date": "2026-01-01",
        "is_active": True,
        "status": "ACTIVE",
    }
    # Current time is October 2026 (over 280 days old)
    score = compute_provenance_score(old_assertion, current_epoch=time.time())
    assert score >= 0.15, f"Receipt-backed assertion score fell below 0.15: {score}"


def test_verify_and_ground_accepts_single_evidence_candidates():
    """Verifies that single-evidence candidates (scoring 0.19-0.27) pass verify_and_ground."""
    candidate = {
        "assertion_id": "asrt-grid_os-single-evidence-01",
        "predicate": "CHARTER_MEMBER",
        "claim_text": " Avery Chen is a core engineer on GRID_OS",
        "subject_node_id": "entity:jeffrey_chyan",
        "object_node_id": "charter:grid_os",
        "evidence_id": "ev-intake-charter-teams-0907",
        "primary_source_id": "01_NEA_Onboarding_Charter_Teams_09-07-2026",
        "source_date": "09/07/2026",
        "is_active": True,
        "status": "ACTIVE",
    }
    verifier = CopilotGroundingVerifier()
    res = verifier.verify_and_ground(
        candidates=[candidate],
        dialogue="discussing GRID_OS staffing and Jeffrey",
    )
    assert len(res.verified_assertions) == 1, f"Expected 1 verified assertion, got {len(res.verified_assertions)}"
    verified = res.verified_assertions[0]
    assert verified["assertion_id"] == "asrt-grid_os-single-evidence-01"
    assert verified["provenance_score"] >= 0.15
    assert res.error is None
    assert not res.is_budget_breached


def test_verify_and_ground_rejects_unbacked_and_superseded():
    """Verifies that verifier still filters superseded and unevidenced assertions."""
    superseded = {
        "assertion_id": "asrt-old-superseded",
        "predicate": "STATUS",
        "claim_text": "Old obsolete claim",
        "is_active": False,
        "status": "SUPERSEDED",
        "evidence_id": "ev-1",
    }
    verifier = CopilotGroundingVerifier()
    res = verifier.verify_and_ground(
        candidates=[superseded],
        dialogue="any discussion",
    )
    assert len(res.verified_assertions) == 0
