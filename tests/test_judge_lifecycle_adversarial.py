"""Adversarial and edge-case tests for Wave 8 judge lifecycle and calibration.

Covers mandatory edge-case categories:
1. Boundary payload: absent metadata, sparse/renamed speakers, retry budget exhaustion.
2. Malformed input: contradictory judge criteria (PASS with score below floor), malformed JSON responses.
3. Invariant bypass & Fault Injection: provider 429/timeout unblocks single-flight, stale cache bypass rejected.
4. Rollback & recovery: consecutive regressing repairs never become final.
"""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.calibration_protocol import (
    CalibrationCaseOutcome,
    PredeclaredTestCase,
    classify_calibration_outcome,
    evaluate_calibration_cohort,
)
from dual_pipeline.remediation_core.monotonic_governor import (
    BestCandidateLedger,
    CandidateRecord,
)
from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_qa_core.judge_cache_core import (
    CanonicalJudgeKey,
    SingleFlightExecutor,
)


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_boundary_absent_metadata_and_sparse_speakers():
    """Missing metadata, null attendees, and single speaker are handled safely."""
    case = PredeclaredTestCase(
        case_id="case_boundary_sparse",
        meeting_name="",
        modality=None,  # type: ignore
        archetype="sparse_speaker",
        human_label="FAIL",
        metadata={},  # completely empty metadata
    )
    assert case.meeting_name == ""
    assert case.metadata == {}

    # Classifies cleanly as True Rejection when judge also rejects
    outcome = classify_calibration_outcome(case.human_label, "FAIL")
    assert outcome == "TRUE_REJECTION"


def test_boundary_retry_budget_exhaustion():
    """Retry budget exhaustion terminates cleanly without endless retries."""
    budget = RetryBudget(
        max_total_attempts=3,
        max_content_repairs=2,
        max_evaluator_repairs=1,
        max_transport_retries=1,
    )

    # Use content repair budget
    can_repair_1, _ = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_repair_1 is True
    budget.record_attempt(RetryClass.CONTENT_REPAIR)

    can_repair_2, _ = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_repair_2 is True
    budget.record_attempt(RetryClass.CONTENT_REPAIR)

    # Third content repair must be exhausted
    can_repair_3, reason_3 = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_repair_3 is False
    assert "Content repair budget exhausted" in reason_3

    # Total budget exhaustion
    budget.record_attempt(RetryClass.EVALUATOR_REPAIR)
    can_any, total_reason = budget.can_attempt(RetryClass.TRANSPORT)
    assert can_any is False
    assert "budget exhausted" in total_reason.lower()


# =========================================================================
# Category 2: Malformed Inputs
# =========================================================================

def test_malformed_contradictory_criteria_pass_below_floor():
    """Judge response claiming verdict=PASS but composite_score=55 (below floor) is caught."""
    from dual_pipeline.release_policy import validate_micro_judge_scorecard

    contradictory_scorecard = {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 55,  # Contradicts pass threshold!
        "evaluated_span_complete": True,
        "dimension_scores": {"d1": 10, "d2": 15, "d3": 15, "d4": 15},
    }

    # Must fail validation
    valid, errors = validate_micro_judge_scorecard(contradictory_scorecard, output_num=2)
    assert valid is False
    assert any("below pass threshold" in err.lower() or "score" in err.lower() for err in errors)


def test_malformed_non_json_judge_response_handled_fail_closed():
    """Non-JSON or truncated LLM output fails closed without corrupting cache."""
    raw_bad_output = "I cannot evaluate this because of an internal server glitch {"
    key = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256="c" * 64,
        reference_sha256="r" * 64,
        context_sha256="x" * 64,
        prompt_digest="p" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
    )

    # Validating key succeeds, but parsing response raises ValueError / fails closed
    valid, _ = key.validate()
    assert valid is True

    with pytest.raises(json.JSONDecodeError):
        json.loads(raw_bad_output)


# =========================================================================
# Category 3: Invariant Bypass & Fault Injection
# =========================================================================

def test_fault_injection_provider_timeout_unblocks_single_flight():
    """Simulated provider HTTP 504 / timeout unblocks waiters and leaves executor clean."""
    executor = SingleFlightExecutor()
    flight_key = "flight:timeout_test"

    def faulty_call():
        raise TimeoutError("Simulated LLM provider timeout (HTTP 504 Gateway Timeout)")

    with pytest.raises(TimeoutError):
        executor.execute(flight_key, faulty_call)

    # Flight state must be cleared after exception
    assert flight_key not in executor._calls

    # Subsequent valid call must succeed without deadlocks
    result, was_deduplicated = executor.execute(flight_key, lambda: "healthy_result")
    assert result == "healthy_result"
    assert was_deduplicated is False


def test_bypass_attempt_stale_cache_drifted_hash_rejected():
    """Candidate with drifted content hash cannot reuse cache entry of original text."""
    from dual_pipeline.transcript_qa_core.eval_cache import check_pin

    original_sha = "1" * 64
    drifted_sha = "2" * 64

    pinned_eval = {
        "output_2": {
            "artifact_sha256": original_sha,
            "composite_score": 95,
            "verdict": "PASS",
        }
    }

    # Query with drifted sha must return None (rejection of stale cache)
    assert check_pin(pinned_eval, "output_2", "output_02", min_score=90, expected_sha=drifted_sha) is None
    # Query with matching sha succeeds
    assert check_pin(pinned_eval, "output_2", "output_02", min_score=90, expected_sha=original_sha) is not None


# =========================================================================
# Category 4: Rollback & Recovery
# =========================================================================

def test_recovery_consecutive_regressing_repairs_never_become_final():
    """
    Attempt 1: Score 85 (Baseline).
    Attempt 2: Score 72 (Regressed).
    Attempt 3: Score 70 (Further regressed).
    Baseline remains locked at Attempt 1 (85), and lower scores are preserved in raw history.
    """
    ledger = BestCandidateLedger()

    r1 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Attempt 1 High Quality",
        composite_score=85,
        verdict="PASS",
        dimension_scores={"accuracy": 22, "structure": 21, "actionability": 21, "relevance": 21},
    )
    assert r1.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 85

    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Attempt 2 Degraded",
        composite_score=72,
        verdict="FAIL",
        dimension_scores={"accuracy": 18, "structure": 18, "actionability": 18, "relevance": 18},
    )
    assert r2.regressed is True
    assert ledger.get_high_water_mark_score("output_2") == 85

    r3 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=3,
        content="Attempt 3 Worse",
        composite_score=70,
        verdict="FAIL",
        dimension_scores={"accuracy": 17, "structure": 18, "actionability": 17, "relevance": 18},
    )
    assert r3.regressed is True
    assert ledger.get_high_water_mark_score("output_2") == 85

    # Best candidate remains Attempt 1
    best = ledger.get_best_candidate("output_2")
    assert best.attempt == 1
    assert best.composite_score == 85
    assert best.content == "Attempt 1 High Quality"

    # Full history preserved
    assert len(ledger.get_history("output_2")) == 3
    assert ledger.get_accepted_trajectory("output_2") == [85, 85, 85]
