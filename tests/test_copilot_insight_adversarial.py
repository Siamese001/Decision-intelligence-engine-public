"""
tests/test_copilot_insight_adversarial.py

Adversarial and edge-case testing for Typed Insight Synthesis under Wave 5.
Covers 4 mandatory edge-case categories from .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: empty assertions list, None fields, 0 max_words limit.
2. Malformed input & injection safety: prohibited markup tags, XSS brackets, code fences, multiple '?'.
3. Overflow & budget exhaustion: 150-word oversized claims, 500 candidate load, tight word budgets.
4. Exact production regression: Voice A vector database memory scaling risk synthesis.
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.cli.copilot_speech import (
    render_speaker_comments,
    synthesize_typed_insight_comment,
)
from dual_pipeline.live_meeting_recorder_core.copilot_insight import (
    build_insight_candidate,
    select_best_insight,
)
from evals.copilot_scorecard import _check_speakability


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - empty assertions, None values, 0 max_words."""
    # Empty assertions list returns clean empty result without crashing
    res_empty = synthesize_typed_insight_comment(
        assertions=[],
        dialogue_context="Any dialogue",
        target=None,
        selected_utterance=None,
        max_words=40,
    )
    assert res_empty.rendered_count == 0
    assert res_empty.body == ""

    # None claim text returns None candidate
    cand_none = build_insight_candidate(
        assertion={"predicate": "HAS_RISK"},
        claim_text="",
        context_tokens=set(),
    )
    assert cand_none is None

    # max_words = 0 enforces 0 words allowed -> exhausts budget immediately
    valid_asrt = {
        "assertion_id": "ast-test-1",
        "predicate": "HAS_RISK",
        "claim_text": "Disk subsystem saturation causes query timeouts.",
    }
    res_zero = synthesize_typed_insight_comment(
        assertions=[valid_asrt],
        dialogue_context="saturation timeouts",
        target="Alice",
        selected_utterance=None,
        max_words=0,
    )
    assert res_zero.rendered_count == 0
    assert res_zero.failure_reason == "WORD_BUDGET_EXHAUSTED"
    assert res_zero.body == ""


def test_adversarial_malformed_input_and_markup_injection():
    """Category 2: Malformed input & injection safety - markup tags, backticks, multiple question marks."""
    # Injected prohibited tags, HTML, and code blocks
    injected_claim = "[PD Conduit / LENS] <script>alert(1)</script> ```drop database``` Service failover policy requires manual DNS swap?"
    asrt = {
        "assertion_id": "ast-inject-1",
        "predicate": "CONFLICTS_WITH",
        "claim_text": injected_claim,
        "object_literal": injected_claim,
    }

    res = synthesize_typed_insight_comment(
        assertions=[asrt],
        dialogue_context="failover DNS swap",
        target="Bob",
        selected_utterance={"speaker": "Alice```evil```", "text": "What about failover?"},
        max_words=40,
    )

    assert res.rendered_count == 1
    comment = res.body

    # Strictly no markup tags or code blocks
    assert "[" not in comment and "]" not in comment
    assert "<" not in comment and ">" not in comment
    assert "```" not in comment
    assert "alert" not in comment or "<script>" not in comment

    # Exactly <= 1 question mark overall
    q_count = comment.count("?")
    assert q_count <= 1, f"Expected <= 1 question mark, got {q_count} in: {comment}"

    # Speakability check passes
    is_spk, reasons = _check_speakability(comment, max_words=40)
    assert is_spk is True, f"Speakability failed on sanitized injection: {reasons}"


def test_adversarial_overflow_and_budget_exhaustion():
    """Category 3: Overflow & exhaustion - massive 150-word claim text failing closed."""
    massive_claim = " ".join(["operational dependency"] * 75)  # 150 words
    asrt = {
        "assertion_id": "ast-huge-1",
        "predicate": "HAS_RISK",
        "claim_text": massive_claim,
    }

    res = synthesize_typed_insight_comment(
        assertions=[asrt],
        dialogue_context="operational dependency",
        target="Charlie",
        selected_utterance=None,
        max_words=40,
    )
    # Must fail closed with zero words spoken rather than exceeding budget
    assert res.rendered_count == 0
    assert res.failure_reason in ("WORD_BUDGET_EXHAUSTED", "REDUNDANT_OR_LOW_NOVELTY")
    assert res.body == ""

    # Stress test: evaluate 300 candidates rapidly
    many_asrts = [
        {
            "assertion_id": f"ast-batch-{i}",
            "predicate": "HAS_RISK" if i % 2 == 0 else "DEFINES_POLICY",
            "claim_text": f"Service cluster {i} latency threshold is {i}ms.",
        }
        for i in range(300)
    ]
    best = select_best_insight(many_asrts, dialogue_context="Service cluster 150 latency threshold", max_words=40)
    assert best is not None
    assert "cluster 150" in best.spoken_comment


def test_exact_production_regression_voice_a_vector_db():
    """Category 4: Exact production regression - Voice A at 07:40 vector DB memory scaling."""
    utterance = {
        "speaker": "Voice A",
        "text": "And you can always set up a Vector database",
        "timestamp": "00:07:40",
    }
    assertions = [
        {
            "assertion_id": "ast-prod-regression-1",
            "subject_node_id": "entity-vdb",
            "predicate": "HAS_RISK",
            "claim_text": "Vector database memory scaling introduces latency spikes during peak indexing.",
        }
    ]

    res = synthesize_typed_insight_comment(
        assertions=assertions,
        dialogue_context=utterance["text"],
        target="Mitch",
        selected_utterance=utterance,
        max_words=40,
    )

    assert res.rendered_count == 1
    comment = res.body

    # Spoken delivery connects target and speaker attribution
    assert "Mitch, regarding Voice A's point" in comment
    assert "vector database memory scaling introduces latency spikes" in comment.lower()
    assert "How are we mitigating this dependency?" in comment

    # Validated speakable format
    is_spk, reasons = _check_speakability(comment, max_words=40)
    assert is_spk is True, f"Production regression comment failed speakability: {reasons}"


def test_adversarial_repeated_commentary_rejection():
    """Verify that under identical context, a duplicate assertion is rejected for an alternative novel one."""
    asrt_primary = {
        "assertion_id": "ast-slo-1",
        "predicate": "DEFINES_POLICY",
        "claim_text": "Gateway availability commitment is 99.99%.",
    }
    asrt_alternative = {
        "assertion_id": "ast-risk-2",
        "predicate": "HAS_RISK",
        "claim_text": "Failover gateway has a two minute warm-up penalty.",
    }

    dialogue = "What are the gateway availability guarantees?"

    # Turn 1: Primary SLO chosen
    res1 = synthesize_typed_insight_comment(
        assertions=[asrt_primary, asrt_alternative],
        dialogue_context=dialogue,
        target="Dana",
        selected_utterance={"speaker": "Evan", "text": dialogue},
        max_words=40,
        recent_history=[],
    )
    assert res1.rendered_count == 1

    # Turn 2: Primary SLO is in recent history, must select alternative risk insight
    recent = [res1.body]
    res2 = synthesize_typed_insight_comment(
        assertions=[asrt_primary, asrt_alternative],
        dialogue_context=dialogue,
        target="Dana",
        selected_utterance={"speaker": "Evan", "text": dialogue},
        max_words=40,
        recent_history=recent,
    )
    assert res2.rendered_count == 1
    # Switched to the novel alternative
    assert "two minute warm-up penalty" in res2.body.lower()
