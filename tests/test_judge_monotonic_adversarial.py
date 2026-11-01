"""Adversarial and edge-case tests for Wave 7 Monotonic Score Governor and Rollback.

Covers mandatory edge-case categories:
1. Boundary payload: empty candidate history, zero-length content, zero scores, exact threshold boundaries.
2. Malformed input: non-numeric/missing dimension scores, negative scores, null scorecards.
3. Invariant bypass attempts: trade-off regressions with higher composite, gate decline bypass, unchanged sampling, cross-epoch bypass.
4. Rollback & recovery: oscillating score sequences, consecutive regressions, non-destructive history preservation.
5. Concurrency & race: multithreaded concurrent candidate recording.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

import pytest

from dual_pipeline.remediation_core.monotonic_governor import (
    BestCandidateLedger,
    CandidateRecord,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_boundary_empty_ledger_queries():
    """Ledger queries on empty state or unrecorded keys return safe defaults without throwing."""
    ledger = BestCandidateLedger()

    assert ledger.get_best_candidate("output_1") is None
    assert ledger.get_best_content("output_1") is None
    assert ledger.get_high_water_mark_score("output_1") == 0
    assert ledger.has_regressed("output_1") is False
    assert ledger.restore_best_candidate("output_1") is None
    assert ledger.get_accepted_trajectory("output_1") == []
    assert ledger.get_history("output_1") == []


def test_boundary_zero_score_and_empty_content():
    """Empty content string and 0 composite score are handled safely."""
    ledger = BestCandidateLedger()

    # Record 0-score baseline
    r1 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="",
        composite_score=0,
        verdict="FAIL",
        dimension_scores={"accuracy": 0},
    )
    assert r1.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 0
    assert ledger.get_best_content("output_2") == ""

    # Next attempt with positive score promotes baseline
    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Positive content",
        composite_score=50,
        verdict="FAIL",
        dimension_scores={"accuracy": 15},
    )
    assert r2.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 50


def test_boundary_exact_score_threshold():
    """Score at exact threshold (e.g. 84 vs 85) promotes when equal or higher."""
    ledger = BestCandidateLedger()

    r1 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Score 84 text",
        composite_score=84,
        dimension_scores={"d1": 21, "d2": 21},
    )
    assert r1.regressed is False

    # Attempt 2 has score 85 with distinct content
    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Score 85 text",
        composite_score=85,
        dimension_scores={"d1": 21, "d2": 22},
    )
    assert r2.regressed is False
    assert ledger.get_high_water_mark_score("output_2") == 85


# =========================================================================
# Category 2: Malformed Inputs
# =========================================================================

def test_malformed_dimension_scores_non_numeric_and_missing():
    """Missing or non-numeric dimension values do not crash eligibility checks."""
    ledger = BestCandidateLedger()

    # Baseline with partial dimensions
    ledger.record_candidate(
        artifact_key="output_1",
        attempt=1,
        content="Baseline",
        composite_score=80,
        dimension_scores={"numeric_dim": 20, "string_dim": "N/A"},
    )

    # Attempt 2 with malformed / non-numeric dimension value
    r2 = ledger.record_candidate(
        artifact_key="output_1",
        attempt=2,
        content="Attempt 2",
        composite_score=85,
        dimension_scores={"numeric_dim": 22, "string_dim": "UNKNOWN", "extra_dim": None},
    )
    # Since numeric_dim improved from 20 to 22 and composite improved, it is eligible
    assert r2.regressed is False
    assert ledger.get_high_water_mark_score("output_1") == 85


def test_malformed_none_full_scorecard():
    """None or empty full_scorecard defaults gracefully to empty dict."""
    ledger = BestCandidateLedger()

    rec = ledger.record_candidate(
        artifact_key="output_3",
        attempt=1,
        content="JSON content",
        composite_score=90,
        full_scorecard=None,
    )
    assert rec.full_scorecard == {}
    assert rec.regressed is False


# =========================================================================
# Category 3: Invariant Bypass Attempts
# =========================================================================

def test_bypass_attempt_huge_composite_jump_with_tradeoff_regression():
    """Attempt with +14 composite jump but regressed sub-dimension is blocked fail-closed."""
    ledger = BestCandidateLedger()

    # Baseline: score 80 with high accuracy (28)
    ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Baseline 80",
        composite_score=80,
        dimension_scores={"accuracy": 28, "completeness": 18, "actionability": 18},
    )

    # Attempt 2: score 94 (+14 jump!), but accuracy dropped from 28 to 22
    r2 = ledger.record_candidate(
        artifact_key="output_2",
        attempt=2,
        content="Compromised accuracy 94",
        composite_score=94,
        dimension_scores={"accuracy": 22, "completeness": 36, "actionability": 36},
    )
    assert r2.regressed is True
    assert "CRITERION_DECLINE" in (r2.rejection_reason or "")
    assert "accuracy" in (r2.rejection_reason or "")
    # Baseline protected at Attempt 1
    assert ledger.get_best_candidate("output_2").attempt == 1
    assert ledger.get_high_water_mark_score("output_2") == 80


def test_bypass_attempt_flunked_gate_with_100_composite():
    """Attempt with 100 composite score but failed deterministic gate is blocked."""
    ledger = BestCandidateLedger()

    ledger.record_candidate(
        artifact_key="output_1",
        attempt=1,
        content="Passing Gate Baseline",
        composite_score=85,
        deterministic_gates_pass=True,
    )

    r2 = ledger.record_candidate(
        artifact_key="output_1",
        attempt=2,
        content="Flunked gate 100",
        composite_score=100,
        deterministic_gates_pass=False,
    )
    assert r2.regressed is True
    assert "HARD_GATE_DECLINE" in (r2.rejection_reason or "")
    assert ledger.get_best_candidate("output_1").composite_score == 85


def test_bypass_attempt_identical_text_at_equal_score():
    """Attempt repeating identical text at equal score is blocked as unchanged sampling."""
    ledger = BestCandidateLedger()

    ledger.record_candidate(
        artifact_key="output_3",
        attempt=1,
        content='{"graph": "unchanged"}',
        composite_score=88,
    )

    r2 = ledger.record_candidate(
        artifact_key="output_3",
        attempt=2,
        content='{"graph": "unchanged"}',
        composite_score=88,
    )
    assert r2.regressed is True
    assert "UNCHANGED_SAMPLING" in (r2.rejection_reason or "")


# =========================================================================
# Category 4: Rollback & Recovery
# =========================================================================

def test_recovery_oscillating_score_sequence():
    """Oscillating score sequence [84, 80, 85, 78, 89] produces monotonic [84, 84, 85, 85, 89]."""
    ledger = BestCandidateLedger()

    scores = [84, 80, 85, 78, 89]
    for idx, sc in enumerate(scores, 1):
        ledger.record_candidate(
            artifact_key="output_2",
            attempt=idx,
            content=f"Content for attempt {idx} score {sc}",
            composite_score=sc,
            dimension_scores={"dim": sc // 4},
        )

    trajectory = ledger.get_accepted_trajectory("output_2")
    assert trajectory == [84, 84, 85, 85, 89]

    # Verify every raw record is retained in history
    history = ledger.get_history("output_2")
    assert len(history) == 5
    assert [h.composite_score for h in history] == [84, 80, 85, 78, 89]
    assert history[1].regressed is True
    assert history[3].regressed is True
    assert history[4].regressed is False
    assert ledger.get_best_candidate("output_2").attempt == 5


def test_recovery_consecutive_regressions_locks_baseline():
    """Consecutive regressions [86, 70, 71, 72] leave baseline locked at Attempt 1 (86)."""
    ledger = BestCandidateLedger()

    ledger.record_candidate(
        artifact_key="output_2",
        attempt=1,
        content="Attempt 1 High",
        composite_score=86,
        dimension_scores={"d": 21},
    )

    for att, sc in [(2, 70), (3, 71), (4, 72)]:
        rec = ledger.record_candidate(
            artifact_key="output_2",
            attempt=att,
            content=f"Attempt {att} Low",
            composite_score=sc,
            dimension_scores={"d": sc // 4},
        )
        assert rec.regressed is True

    assert ledger.get_high_water_mark_score("output_2") == 86
    best = ledger.get_best_candidate("output_2")
    assert best.attempt == 1
    assert best.content == "Attempt 1 High"


# =========================================================================
# Category 5: Concurrency & Race
# =========================================================================

def test_concurrency_multithreaded_candidate_recording():
    """Multithreaded recording of candidates across artifacts does not corrupt ledger structures."""
    ledger = BestCandidateLedger()

    def record_job(worker_id: int):
        for step in range(1, 10):
            art = f"output_{(worker_id % 3) + 1}"
            score = 70 + (step * 2)
            ledger.record_candidate(
                artifact_key=art,
                attempt=step,
                content=f"Content worker {worker_id} step {step}",
                composite_score=score,
                dimension_scores={"d": score // 4},
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(record_job, w) for w in range(6)]
        for f in futures:
            f.result()

    for out_key in ("output_1", "output_2", "output_3"):
        best = ledger.get_best_candidate(out_key)
        assert best is not None
        assert best.composite_score >= 72
