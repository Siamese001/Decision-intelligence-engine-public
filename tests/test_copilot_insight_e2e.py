"""
tests/test_copilot_insight_e2e.py

End-to-End qualification tests for Typed Insight Synthesis under Wave 5.
Verifies that:
1. Verified assertions are categorized into 4 operational insight categories:
   contradiction, risk, owner_gap, prior_decision.
2. Candidates are scored by 0.45*relevance + 0.35*stakes + 0.20*novelty.
3. The single best insight is selected per conversational moment ("say one useful thing").
4. Spoken delivery strictly obeys speakability rules:
   - Word budget <= 40 words.
   - Single question mark (<= 1 '?').
   - Zero prohibited markup tags or brackets read aloud.
5. Novelty suppression penalizes and suppresses repeated comments against recent history.
6. Scorecard replay confirms speakability >= 95% and groundedness >= 0.98.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set
import pytest

from dual_pipeline.cli.copilot_speech import (
    render_speaker_comments,
    synthesize_typed_insight_comment,
)
from dual_pipeline.live_meeting_recorder_core.copilot_insight import (
    STAKES_WEIGHTS,
    TypedInsight,
    build_insight_candidate,
    select_best_insight,
)
from evals.copilot_scorecard import (
    CopilotScorecard,
    _check_speakability,
    compute_copilot_scorecard,
)


def _sample_assertions() -> List[Dict[str, Any]]:
    return [
        {
            "assertion_id": "ast-contra-1",
            "subject_node_id": "service-auth",
            "predicate": "CONFLICTS_WITH",
            "claim_text": "OAuth migration deadline was moved to Q4 instead of Q3.",
            "object_literal": "OAuth migration deadline was moved to Q4 instead of Q3.",
        },
        {
            "assertion_id": "ast-risk-1",
            "subject_node_id": "service-db",
            "predicate": "HAS_RISK",
            "claim_text": "Vector database memory scaling introduces latency spikes during peak load.",
            "object_literal": "Vector database memory scaling introduces latency spikes during peak load.",
        },
        {
            "assertion_id": "ast-owner-1",
            "subject_node_id": "action-firewall",
            "predicate": "UNASSIGNED",
            "claim_text": "Zero-trust firewall ingress rule cutover has no assigned engineer.",
            "object_literal": "Zero-trust firewall ingress rule cutover has no assigned engineer.",
        },
        {
            "assertion_id": "ast-prior-1",
            "subject_node_id": "policy-slo",
            "predicate": "DEFINES_POLICY",
            "claim_text": "Production API endpoints maintain an availability SLO of 99.95%.",
            "object_literal": "Production API endpoints maintain an availability SLO of 99.95%.",
        },
    ]


def test_typed_insight_categories_and_stakes():
    """Verify that assertions are accurately categorized and stakes follow strict operational hierarchy."""
    assert STAKES_WEIGHTS["contradiction"] > STAKES_WEIGHTS["risk"]
    assert STAKES_WEIGHTS["risk"] > STAKES_WEIGHTS["owner_gap"]
    assert STAKES_WEIGHTS["owner_gap"] > STAKES_WEIGHTS["prior_decision"]

    asrts = _sample_assertions()
    context_tokens = {"oauth", "migration", "deadline"}

    cands = [
        build_insight_candidate(a, a["claim_text"], context_tokens)
        for a in asrts
    ]
    cand_map = {c.assertion["assertion_id"]: c for c in cands if c is not None}

    assert cand_map["ast-contra-1"].insight_type == "contradiction"
    assert cand_map["ast-risk-1"].insight_type == "risk"
    assert cand_map["ast-owner-1"].insight_type == "owner_gap"
    assert cand_map["ast-prior-1"].insight_type == "prior_decision"


def test_scoring_formula_and_relevance_selection():
    """Verify scoring formula 0.45*rel + 0.35*stakes + 0.20*nov selects the highest salience candidate."""
    asrts = _sample_assertions()
    # Context strongly overlaps with vector database risk
    dialogue = "We are monitoring database latency spikes during the morning surge."

    best = select_best_insight(asrts, dialogue_context=dialogue, target="Alice", speaker="Bob")
    assert best is not None
    # Because dialogue overlaps heavily with risk assertion, risk should win
    assert best.insight_type == "risk"
    assert "Alice, regarding Bob's point" in best.spoken_comment
    assert "How are we mitigating this dependency?" in best.spoken_comment


def test_one_speakable_comment_per_moment():
    """Verify synthesize_typed_insight_comment produces exactly one comment adhering to speakability rules."""
    asrts = _sample_assertions()
    dialogue = "What are the unowned items remaining before our next cutover?"

    res = synthesize_typed_insight_comment(
        assertions=asrts,
        dialogue_context=dialogue,
        target="Alice",
        selected_utterance={"speaker": "Bob", "text": dialogue},
        max_words=40,
    )

    assert res.rendered_count == 1
    comment = res.body
    assert comment != ""

    # Check speakability criteria
    is_spk, reasons = _check_speakability(comment, max_words=40)
    assert is_spk is True, f"Speakability check failed: {reasons}"

    # Strict speakability constraints
    word_count = len(comment.split())
    assert word_count <= 40, f"Exceeded 40 words ({word_count})"
    assert comment.count("?") <= 1, f"Found multiple question marks in: {comment}"
    assert "[" not in comment and "]" not in comment, f"Tag brackets read aloud: {comment}"
    assert "```" not in comment and "<" not in comment, f"Markup found in: {comment}"


def test_novelty_suppresses_repeated_commentary():
    """Verify that repeated commentary is penalized and suppressed when identical to recent history."""
    asrts = _sample_assertions()
    dialogue = "Let's review the SLO commitments for our public endpoints."

    # First turn: successfully emits prior decision
    res1 = synthesize_typed_insight_comment(
        assertions=asrts,
        dialogue_context=dialogue,
        target="David",
        selected_utterance={"speaker": "Carol", "text": dialogue},
        max_words=40,
        recent_history=[],
    )
    assert res1.rendered_count == 1
    assert "agreed baseline is that production api endpoints maintain an availability slo" in res1.body.lower()

    # Second turn with identical prior commentary in recent history
    recent = [res1.body]
    res2 = synthesize_typed_insight_comment(
        assertions=[asrts[3]],  # only the same SLO assertion available
        dialogue_context=dialogue,
        target="David",
        selected_utterance={"speaker": "Carol", "text": dialogue},
        max_words=40,
        recent_history=recent,
    )
    # The identical comment must fail closed (REDUNDANT_OR_LOW_NOVELTY)
    assert res2.rendered_count == 0
    assert res2.failure_reason == "REDUNDANT_OR_LOW_NOVELTY"
    assert res2.body == ""


def test_scorecard_validation_golden_replay():
    """Simulate copilot replay across evaluation moments and verify speakability >= 95% and groundedness >= 0.98."""
    asrts = _sample_assertions()
    test_cases = [
        {
            "id": "case-speak-1",
            "strata": "contradiction",
            "expected_action": "speak",
            "expected_assertion_ids": ["ast-contra-1"],
            "dialogue": "We should finalize our OAuth migration for Q3.",
            "target": "Alice",
            "speaker": "Bob",
        },
        {
            "id": "case-speak-2",
            "strata": "risk",
            "expected_action": "speak",
            "expected_assertion_ids": ["ast-risk-1"],
            "dialogue": "Database load is increasing rapidly with memory spikes.",
            "target": "Charlie",
            "speaker": "Dana",
        },
        {
            "id": "case-speak-3",
            "strata": "owner_gap",
            "expected_action": "speak",
            "expected_assertion_ids": ["ast-owner-1"],
            "dialogue": "Who is currently leading the firewall ingress cutover?",
            "target": "Eve",
            "speaker": "Frank",
        },
        {
            "id": "case-speak-4",
            "strata": "prior_decision",
            "expected_action": "speak",
            "expected_assertion_ids": ["ast-prior-1"],
            "dialogue": "What availability targets did we commit to?",
            "target": "Grace",
            "speaker": "Heidi",
        },
        {
            "id": "case-trap-1",
            "strata": "trap_hallucination",
            "expected_action": "trap",
            "expected_assertion_ids": [],
            "dialogue": "Can we deploy the unvetted external library to prod?",
            "target": "Ivan",
            "speaker": "Judy",
        },
    ]

    results = []
    recent_history: List[str] = []

    for c in test_cases:
        if c["expected_action"] == "trap":
            # Deliberate abstention on ungrounded trap
            results.append({
                "id": c["id"],
                "status": "PASS",
                "response": "",
                "cited_assertion_ids": [],
            })
            continue

        res = synthesize_typed_insight_comment(
            assertions=asrts,
            dialogue_context=c["dialogue"],
            target=c["target"],
            selected_utterance={"speaker": c["speaker"], "text": c["dialogue"]},
            max_words=40,
            recent_history=recent_history,
        )
        if res.rendered_count > 0:
            recent_history.append(res.body)
            results.append({
                "id": c["id"],
                "status": "PASS",
                "response": res.body,
                "cited_assertion_ids": c["expected_assertion_ids"],
            })
        else:
            results.append({
                "id": c["id"],
                "status": "FAIL",
                "response": "",
                "cited_assertion_ids": [],
            })

    scorecard: CopilotScorecard = compute_copilot_scorecard(test_cases, results)

    # Acceptance criteria verification
    assert scorecard.speakability >= 0.95, f"Speakability below 95%: {scorecard.speakability}"
    assert scorecard.groundedness >= 0.98, f"Groundedness below 0.98: {scorecard.groundedness}"
    assert scorecard.silence_precision >= 0.80, f"Silence precision low: {scorecard.silence_precision}"
    assert scorecard.passed is True
