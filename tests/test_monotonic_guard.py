"""
Unit tests for Monotonic Progress Guard (Wave 4).
"""

from __future__ import annotations

from dual_pipeline.transcript_qa_core.monotonic_guard import MonotonicGuard


def test_monotonic_guard_initial_baseline() -> None:
    """Tests that the initial candidate sets the baseline."""
    guard = MonotonicGuard(artifact_name="OUTPUT02")
    text, sc, improved = guard.evaluate_candidate(
        attempt=1,
        composite_score=86,
        artifact_text="Attempt 1 Text",
        scorecard={"composite_score": 86, "verdict": "PASS_ADVISORY"},
    )
    assert text == "Attempt 1 Text"
    assert guard.best_score == 86
    assert improved is True


def test_monotonic_guard_progress_and_regression_rollback() -> None:
    """Tests that improvement updates best score and regression rolls back immediately."""
    guard = MonotonicGuard(artifact_name="OUTPUT02")

    # Attempt 1: Baseline 86
    guard.evaluate_candidate(
        attempt=1,
        composite_score=86,
        artifact_text="Attempt 1 Text",
        scorecard={"composite_score": 86, "verdict": "PASS_ADVISORY"},
    )

    # Attempt 2: Improved to 96
    text2, sc2, improved2 = guard.evaluate_candidate(
        attempt=2,
        composite_score=96,
        artifact_text="Attempt 2 Text (Passing)",
        scorecard={"composite_score": 96, "verdict": "PASS"},
    )
    assert text2 == "Attempt 2 Text (Passing)"
    assert guard.best_score == 96
    assert improved2 is True

    # Attempt 3: Regressed to 75 (Stochastic Re-roll failure!)
    text3, sc3, improved3 = guard.evaluate_candidate(
        attempt=3,
        composite_score=75,
        artifact_text="Attempt 3 Text (Regressed)",
        scorecard={"composite_score": 75, "verdict": "FAIL"},
    )
    # MUST roll back to Attempt 2!
    assert text3 == "Attempt 2 Text (Passing)"
    assert sc3["composite_score"] == 96
    assert guard.best_score == 96
    assert improved3 is False
