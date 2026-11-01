"""Unit tests for transcript chunking evaluation metrics.

Tests:
1. Boundary reduction calculation and percentage formatting.
2. Turn coverage, attribution accuracy, and true identified share ratios.
3. Content conservation gate (strict word identity and sequence preservation).
4. Frame-accurate time-level diarization error rate (DER) computation.
5. Chunking efficiency analytics (reduction of choppy 1-word and <=3-word fragments).
6. Separation of independent reference labels from candidate hypotheses.
"""
from __future__ import annotations

import pytest

from evals.transcript_chunking_metrics import (
    compute_attribution_accuracy,
    compute_boundary_reduction_ratio,
    compute_chunking_efficiency,
    compute_time_level_metrics,
    compute_true_identified_share,
    compute_turn_coverage,
    verify_content_conservation,
)


def test_boundary_reduction_ratio():
    assert compute_boundary_reduction_ratio(426, 196) == 53.99
    assert compute_boundary_reduction_ratio(100, 50) == 50.0
    assert compute_boundary_reduction_ratio(100, 100) == 0.0
    assert compute_boundary_reduction_ratio(0, 0) == 0.0


def test_turn_coverage_accuracy_and_true_share():
    # 426 turns, 408 attributed, 408 correct, 18 unknown
    N = 426
    M = 408
    C = 408

    cov = compute_turn_coverage(M, N)
    assert cov == 0.9577
    assert cov >= 0.95  # Gate 1

    acc = compute_attribution_accuracy(C, M)
    assert acc == 1.0
    assert acc >= 0.98  # Gate 2

    true_share = compute_true_identified_share(C, N)
    assert true_share == 0.9577
    assert true_share >= 0.931  # Gate 3


def test_verify_content_conservation_passes_on_exact_match():
    raw_turns = [
        {"text": "We need to"},
        {"text": "verify the"},
        {"text": "pipeline results."},
    ]
    assembled = [
        {"text": "We need to verify the pipeline results."},
    ]
    passed, msg = verify_content_conservation(raw_turns, assembled)
    assert passed is True
    assert "100% word conservation verified (7 words)" in msg


def test_verify_content_conservation_fails_on_dropped_or_added_tokens():
    raw_turns = [{"text": "Hello world"}]
    dropped = [{"text": "Hello"}]
    passed, msg = verify_content_conservation(raw_turns, dropped)
    assert passed is False
    assert "Token count mismatch" in msg

    added = [{"text": "Hello beautiful world"}]
    passed, msg = verify_content_conservation(raw_turns, added)
    assert passed is False
    assert "Token count mismatch" in msg


def test_verify_content_conservation_fails_on_reordered_tokens():
    raw_turns = [{"text": "A B C"}]
    reordered = [{"text": "A C B"}]
    passed, msg = verify_content_conservation(raw_turns, reordered)
    assert passed is False
    assert "Token mismatch at index 1" in msg


def test_time_level_der_computation_exact_match():
    ref = [
        {"speaker": "Alice", "start": 0.0, "end": 2.0},
        {"speaker": "Bob", "start": 2.5, "end": 4.5},
    ]
    hyp = [
        {"speaker": "Alice", "start": 0.0, "end": 2.0},
        {"speaker": "Bob", "start": 2.5, "end": 4.5},
    ]
    metrics = compute_time_level_metrics(ref, hyp)
    assert metrics["der"] == 0.0
    assert metrics["confusion_sec"] == 0.0
    assert metrics["missed_sec"] == 0.0
    assert metrics["false_alarm_sec"] == 0.0


def test_time_level_der_computation_with_confusion_and_misses():
    ref = [
        {"speaker": "Alice", "start": 0.0, "end": 2.0},
        {"speaker": "Bob", "start": 2.0, "end": 4.0},
    ]
    # Hypothesis confuses second turn as Charlie and truncates
    hyp = [
        {"speaker": "Alice", "start": 0.0, "end": 2.0},
        {"speaker": "Charlie", "start": 2.0, "end": 3.0},
    ]
    metrics = compute_time_level_metrics(ref, hyp)
    assert metrics["der"] > 0.0
    assert metrics["confusion_sec"] > 0.0
    assert metrics["missed_sec"] > 0.0


def test_chunking_efficiency_analytics():
    raw_turns = [
        {"text": "Yeah"},  # 1w
        {"text": "Okay"},  # 1w
        {"text": "I see"},  # 2w
        {"text": "Let us check the deployment status on staging."},  # 8w
    ]
    assembled = [
        {"text": "Yeah Okay I see"},  # 4w
        {"text": "Let us check the deployment status on staging."},  # 8w
    ]
    eff = compute_chunking_efficiency(raw_turns, assembled)
    assert eff["raw_turn_count"] == 4
    assert eff["assembled_utterance_count"] == 2
    assert eff["boundary_reduction_pct"] == 50.0
    assert eff["raw_single_word_pct"] == 50.0  # 2 of 4
    assert eff["assembled_single_word_pct"] == 0.0  # 0 of 2
    assert eff["assembled_avg_words_per_utterance"] == 6.0
