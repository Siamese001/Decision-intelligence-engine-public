"""Acceptance tests for Wave 7 Monotonic Score Governor and Best Candidate Ledger.

Enforces:
1. Monotonic score progression invariant (Score_{N+1} >= Score_N).
2. Raw 84 -> 81 -> 88 yields accepted trajectory 84 -> 84 -> 88 while retaining raw 81.
3. Criterion-vector eligibility: no dimension decline permitted even if composite increases.
4. Hard gate preservation: no regression in deterministic or QA gates.
5. Unchanged best-of sampling prevention.
6. Epoch invalidation: model/source/policy changes create a visible new epoch.
7. Atomic full-bundle rollback: full scorecard, content, and QA preserved together.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from dual_pipeline.remediation_core.monotonic_governor import (
    BestCandidateLedger,
    CandidateRecord,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


def test_monotonic_trajectory_raw_84_81_88():
    """Raw trajectory 84 -> 81 -> 88 yields accepted trajectory 84 -> 84 -> 88 while keeping raw 81."""
    ledger = BestCandidateLedger(current_epoch="epoch-1")

    # Attempt 1: Raw 84
    r1 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Recap v1 with score 84",
        composite_score=84,
        verdict="FAIL",
        dimension_scores={"accuracy": 21, "completeness": 21, "conciseness": 21, "actionability": 21},
        qa_passed=True,
        deterministic_gates_pass=True,
        full_scorecard={"composite_score": 84, "verdict": "FAIL", "eval_meta": {"run": 1}},
    )
    assert r1.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 84

    # Attempt 2: Raw 81 (regression)
    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Recap v2 with score 81",
        composite_score=81,
        verdict="FAIL",
        dimension_scores={"accuracy": 20, "completeness": 20, "conciseness": 20, "actionability": 21},
        qa_passed=True,
        deterministic_gates_pass=True,
        full_scorecard={"composite_score": 81, "verdict": "FAIL", "eval_meta": {"run": 2}},
    )
    assert r2.regressed is True
    assert any(k in (r2.rejection_reason or "") for k in ("SCORE_DECLINE", "CRITERION_DECLINE"))
    # Baseline remains 84
    assert ledger.get_high_water_mark_score("output_2") == 84
    assert ledger.get_best_candidate("output_2").attempt == 1

    # Attempt 3: Raw 88 (improvement)
    r3 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=3,
        content="Recap v3 with score 88",
        composite_score=88,
        verdict="PASS",
        dimension_scores={"accuracy": 22, "completeness": 22, "conciseness": 22, "actionability": 22},
        qa_passed=True,
        deterministic_gates_pass=True,
        full_scorecard={"composite_score": 88, "verdict": "PASS", "eval_meta": {"run": 3}},
    )
    assert r3.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 88
    assert ledger.get_best_candidate("output_2").attempt == 3

    # Trajectory verification
    trajectory = ledger.get_accepted_trajectory("output_2")
    assert trajectory == [84, 84, 88]

    # Full raw history preserved
    history = ledger.get_history("output_2")
    assert len(history) == 3
    assert [h.composite_score for h in history] == [84, 81, 88]
    assert history[1].content == "Recap v2 with score 81"
    assert history[1].regressed is True


def test_criterion_vector_decline_rejected_despite_higher_total():
    """Higher total composite score is rejected if any comparable dimension declines (e.g. [92, 84, 95] -> [91, 88, 95])."""
    ledger = BestCandidateLedger(current_epoch="epoch-1")

    # Baseline: composite 84, dimensions: dim_a=25, dim_b=20, dim_c=20, dim_d=19
    r1 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Baseline content",
        composite_score=84,
        verdict="FAIL",
        dimension_scores={"dim_a": 25, "dim_b": 20, "dim_c": 20, "dim_d": 19},
        qa_passed=True,
        deterministic_gates_pass=True,
    )
    assert r1.regressed is False

    # Attempt 2: composite 88 (higher!), but dim_a dropped from 25 to 22
    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Higher total but trade-off regression",
        composite_score=88,
        verdict="PASS",
        dimension_scores={"dim_a": 22, "dim_b": 22, "dim_c": 22, "dim_d": 22},
        qa_passed=True,
        deterministic_gates_pass=True,
    )
    assert r2.regressed is True
    assert "CRITERION_DECLINE" in (r2.rejection_reason or "")
    assert "dim_a" in (r2.rejection_reason or "")

    # Baseline remains Attempt 1
    best = ledger.get_best_candidate("output_2")
    assert best.attempt == 1
    assert best.composite_score == 84


def test_hard_gate_decline_rejected_despite_higher_score():
    """Candidate with higher LLM score but failed deterministic gates cannot promote over passing baseline."""
    ledger = BestCandidateLedger(current_epoch="epoch-1")

    # Baseline with passing gates
    ledger.record_candidate(
        artifact_key="output_1",
        attempt=1,
        content="Baseline transcript",
        composite_score=85,
        verdict="PASS",
        deterministic_gates_pass=True,
        qa_passed=True,
    )

    # Attempt 2: Higher score (95), but deterministic gate failed
    r2 = ledger.record_candidate(
        artifact_key="output_1",
        attempt=2,
        content="High score but broken schema",
        composite_score=95,
        verdict="PASS",
        deterministic_gates_pass=False,
        qa_passed=True,
    )
    assert r2.regressed is True
    assert "HARD_GATE_DECLINE" in (r2.rejection_reason or "")
    assert ledger.get_best_candidate("output_1").attempt == 1

    # Attempt 3: Higher score (92), but QA failed
    r3 = ledger.record_candidate(
        artifact_key="output_1",
        attempt=3,
        content="High score but failed QA",
        composite_score=92,
        verdict="PASS",
        deterministic_gates_pass=True,
        qa_passed=False,
    )
    assert r3.regressed is True
    assert "QA_GATE_DECLINE" in (r3.rejection_reason or "")
    assert ledger.get_best_candidate("output_1").attempt == 1


def test_unchanged_sampling_rejected():
    """Candidate with identical content and equal score is rejected as unchanged sampling."""
    ledger = BestCandidateLedger(current_epoch="epoch-1")

    ledger.record_candidate(
        artifact_key="output_3",
        attempt=1,
        content='{"nodes": [], "edges": []}',
        composite_score=90,
        verdict="PASS",
    )

    r2 = ledger.record_candidate(
        artifact_key="output_3",
        attempt=2,
        content='{"nodes": [], "edges": []}',
        composite_score=90,
        verdict="PASS",
    )
    assert r2.regressed is True
    assert "UNCHANGED_SAMPLING" in (r2.rejection_reason or "")


def test_epoch_invalidation_resets_certifications():
    """Epoch invalidation (e.g. policy or model upgrade) starts a visible new comparison epoch."""
    ledger = BestCandidateLedger(current_epoch="epoch-v1")

    ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Old policy candidate",
        composite_score=90,
        verdict="PASS",
    )
    assert ledger.get_high_water_mark_score("output_2") == 90

    # Invalidate epoch due to updated policy
    ledger.invalidate_epoch(new_epoch_id="epoch-v2", reason="Rubric contract updated to v2.1")
    assert ledger.current_epoch == "epoch-v2"
    assert ledger.get_best_candidate("output_2") is None

    # First candidate in new epoch becomes new baseline even if score is lower than old epoch
    r_new = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="New policy candidate",
        composite_score=82,
        verdict="FAIL",
    )
    assert r_new.regressed is False
    assert r_new.epoch_id == "epoch-v2"
    assert ledger.get_high_water_mark_score("output_2") == 82


def test_atomic_full_bundle_preservation():
    """Rollback restores content and full scorecard together atomically."""
    ledger = BestCandidateLedger()

    full_scorecard_1 = {
        "composite_score": 85,
        "verdict": "PASS",
        "dimension_scores": {"d1": 22, "d2": 21},
        "reasoning": "Strong execution",
        "reconciled_claims": ["claim_1", "claim_2"],
    }
    ledger.record_candidate(
        artifact_key="output_1",
        attempt=1,
        content="Good content v1",
        composite_score=85,
        verdict="PASS",
        dimension_scores={"d1": 22, "d2": 21},
        full_scorecard=full_scorecard_1,
    )

    # Regressed attempt 2
    ledger.record_candidate(
        artifact_key="output_1",
        attempt=2,
        content="Degraded content v2",
        composite_score=75,
        verdict="FAIL",
        dimension_scores={"d1": 18, "d2": 19},
        full_scorecard={"composite_score": 75, "verdict": "FAIL"},
    )

    restored_content, restored_record = ledger.restore_best_candidate("output_1")
    assert restored_content == "Good content v1"
    assert restored_record.composite_score == 85
    assert restored_record.full_scorecard == full_scorecard_1
    assert restored_record.full_scorecard["reconciled_claims"] == ["claim_1", "claim_2"]


def test_state_machine_monotonic_integration():
    """State machine integrates ledger, trajectory tracking, and epoch invalidation."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    sm.record_candidate(
        artifact_key="output_2",
        content="Recap Attempt 1",
        composite_score=84,
        verdict="FAIL",
        attempt=1,
        full_scorecard={"composite_score": 84, "verdict": "FAIL"},
    )
    assert sm.get_high_water_mark_score("output_2") == 84

    sm.record_candidate(
        artifact_key="output_2",
        content="Recap Attempt 2 (regressed)",
        composite_score=80,
        verdict="FAIL",
        attempt=2,
        full_scorecard={"composite_score": 80, "verdict": "FAIL"},
    )
    assert sm.has_regressed("output_2") is True
    assert sm.get_high_water_mark_score("output_2") == 84

    # Trajectory remains [84, 84]
    assert sm.get_accepted_trajectory("output_2") == [84, 84]

    # Invalidate epoch
    sm.invalidate_epoch("epoch-20261008", "Policy refreshed")
    assert sm.get_best_candidate("output_2") is None
    assert len(sm.frozen_artifacts) == 0
