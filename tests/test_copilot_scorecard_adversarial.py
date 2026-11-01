"""
tests/test_copilot_scorecard_adversarial.py

Adversarial and edge-case qualification tests for Copilot Replay Harness and Scorecard.
Mandatory Edge-Case Categories covered:
1. Boundary payload: empty input sets, zero latencies, missing responses.
2. Malformed input: non-numeric latencies, missing ids, unknown strata.
3. Overflow & exhaustion: massive word count breach, novelty collapse, latency spikes.
4. Invariant bypass attempt: C-19 career leverage egress blocks and context snapshot quarantine.
5. Exact production regression: raw extraction metadata and markup tag speakability rejection.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import pytest

from dual_pipeline.answer_ledger_core.models import AnswerRecord, sanitize_context_snapshot
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer
from evals.copilot_scorecard import (
    CopilotScorecard,
    _check_novelty,
    _check_speakability,
    compute_copilot_scorecard,
)
from tools.build_copilot_golden import validate_golden_cases


def test_boundary_payload_empty_and_zero_inputs():
    """Category 1: Boundary payload - handles empty sets and zero-latency records cleanly."""
    # Empty cases and results
    empty_sc = compute_copilot_scorecard([], [])
    assert empty_sc.total_cases == 0
    assert empty_sc.reproducible_digest != ""
    assert empty_sc.passes_floors()

    # Cases with zero latencies and empty responses
    cases = [
        {"id": "b_01", "strata": "abstain", "expected_action": "abstain", "prompt": "Q1"},
        {"id": "b_02", "strata": "grounded", "expected_action": "speak", "prompt": "Q2"},
    ]
    results = [
        {"id": "b_01", "status": "PASS", "response": "", "stage_latencies_ms": {"total_ms": 0.0}},
        {"id": "b_02", "status": "FAIL", "response": None, "stage_latencies_ms": {}},
    ]
    sc = compute_copilot_scorecard(cases, results)
    assert sc.total_cases == 2
    assert sc.silence_precision == 1.0
    assert sc.stage_latencies["total_ms"]["max"] == 0.0


def test_malformed_input_resilience():
    """Category 2: Malformed input - gracefully tolerates missing IDs, invalid types, and malformed strata."""
    cases = [
        {"id": "m_01", "strata": "unknown_strata", "expected_action": "INVALID_ACTION", "prompt": "Q1"},
        {"id": "m_02", "strata": None, "expected_action": None, "prompt": "Q2"},
    ]
    results = [
        {"id": "m_01", "status": "PASS", "response": "Valid response", "cited_assertion_ids": ["ast-1"], "stage_latencies_ms": {"total_ms": "not_a_number"}},
        {"case_id": "m_02", "status": "UNKNOWN", "response_text": ""},
    ]
    # Should not crash on invalid action or non-float latency
    sc = compute_copilot_scorecard(cases, results)
    assert sc.total_cases == 2
    assert "unknown_strata" in sc.strata_metrics or "general" in sc.strata_metrics


def test_overflow_and_exhaustion():
    """Category 3: Overflow & exhaustion - massive word budgets, novelty collapse, and latency spikes."""
    # Massive word budget (2,000 words)
    giant_text = "word " * 2000
    valid, reasons = _check_speakability(giant_text)
    assert not valid
    assert any("WORD_COUNT_EXCEEDED" in r for r in reasons)

    # Novelty collapse across repeated responses
    history = [
        "The architecture relies on SQLite WAL journal mode.",
        "The architecture relies on SQLite WAL journal mode.",
        "The architecture relies on SQLite WAL journal mode.",
    ]
    is_novel = _check_novelty("The architecture relies on SQLite WAL journal mode.", history)
    assert not is_novel, "Identical consecutive text must be flagged as non-novel"

    # Extreme latency outlier (500,000 ms)
    cases = [{"id": f"lat_{i}", "strata": "grounded", "expected_action": "speak", "prompt": f"Q{i}"} for i in range(10)]
    results = [
        {
            "id": f"lat_{i}",
            "status": "PASS",
            "response": "Brief answer.",
            "cited_assertion_ids": ["ast_1"],
            "stage_latencies_ms": {"total_ms": 100.0 if i < 9 else 500000.0},
        }
        for i in range(10)
    ]
    sc = compute_copilot_scorecard(cases, results)
    assert sc.stage_latencies["total_ms"]["max"] == 500000.0
    # p95 will capture the extreme spike
    assert sc.stage_latencies["total_ms"]["p95"] > 1000.0


def test_invariant_bypass_and_c19_egress_firewall():
    """Category 4: Invariant bypass attempt - blocks C-19 career leverage and quarantines snapshots."""
    # 1. Direct egress block
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_messages([{"content": "Confidential executive compensation band details"}])

    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_messages([{"content": "Internal advisory political power map"}])

    # 2. Context snapshot quarantine in AnswerRecord
    raw_snapshot = [
        {"speaker": "Voice A", "text": "Normal meeting discussion."},
        {"speaker": "Voice B", "text": "Discussion of career leverage and political maneuver."},
    ]
    clean_snapshot = sanitize_context_snapshot(raw_snapshot)
    assert clean_snapshot[0]["text"] == "Normal meeting discussion."
    assert "CAREER LEVERAGE REDACTED" in clean_snapshot[1]["text"]

    record = AnswerRecord(
        pipeline="copilot",
        intent_or_type="next-questions",
        context_digest="",
        retrieval_seeds=["seed1"],
        context_snapshot=raw_snapshot,
        cited_assertion_ids=[],
        rigor="STANDARD",
        response_text="Clean answer.",
    )
    assert "CAREER LEVERAGE REDACTED" in record.context_snapshot[1]["text"]


def test_exact_production_regressions():
    """Category 5: Production regression - flags raw extraction templates and tag formatting leaks."""
    # Raw extraction template leak regression ("is an identified technical_anchor in")
    cases = [{"id": "reg_01", "strata": "grounded", "expected_action": "speak", "prompt": "Vector DB"}]
    bad_results = [
        {
            "id": "reg_01",
            "status": "PASS",
            "response": "Could we clarify Vector Database is an identified technical_anchor in 20260930 Review?",
            "cited_assertion_ids": ["ast-1"],
        }
    ]
    sc = compute_copilot_scorecard(cases, bad_results)
    assert sc.groundedness == 0.0, "Raw template extraction strings must fail groundedness scoring"

    # Markup leak regression
    valid_markup, reasons = _check_speakability("```json\n{\"error\": true}\n```")
    assert not valid_markup
    assert any("PROHIBITED_MARKUP_DETECTED" in r for r in reasons)
