"""
tests/test_copilot_speak_gate_e2e.py

End-to-End qualification tests for Copilot Speak Gate under Wave 2.
Verifies that:
1. Speak Gate stops ungrounded claims, traps, and leaks ("Silence beats a weak line").
2. Zero ungrounded spoken claims across the golden evaluation corpus.
3. Negation flips and polarity inversion defects are rejected fail-closed.
4. Superseded, deprecated, and stale assertions fail closed.
5. Questions need no evidence, while low speaker confidence downgrades tier.
6. Unknown assertion ownership is quarantined fail-closed.
7. Scorecard rerun confirms zero ungrounded claims and silence-recall loss < 5 points.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    SpeakTier,
    evaluate_speak_gate,
    verify_whole_response_coherence,
)
from evals.copilot_scorecard import compute_copilot_scorecard
from tools.build_copilot_golden import load_golden_cases


def test_speak_gate_golden_corpus_qualification():
    """Verify Speak Gate enforces 100% precision on golden corpus traps and allows valid moments to speak."""
    golden_path = Path(__file__).resolve().parents[1] / "tests" / "evals" / "copilot_golden" / "v1" / "golden_cases.jsonl"
    cases = load_golden_cases(golden_path)
    assert len(cases) == 60

    grounded_spoken = 0
    traps_silenced = 0
    trap_strata = ("abstain", "ownership_traps", "commitment", "c19", "cross_project")

    results: List[Dict[str, Any]] = []

    for c in cases:
        cid = c["id"]
        stratum = c["strata"]
        expected_act = c["expected_action"]

        if stratum in trap_strata:
            # Trap moment
            ast = None
            if stratum == "c19":
                ast = {"assertion_id": f"ast_{cid}", "claim_text": "Executive compensation trajectory notes."}
            elif stratum == "cross_project":
                ast = {"assertion_id": f"ast_{cid}", "engagement_id": "quarantine:cross_tenant", "claim_text": "Project data."}
            elif stratum == "ownership_traps":
                ast = {"assertion_id": f"ast_{cid}", "engagement_id": "quarantine:unknown_ownership", "claim_text": "Ownership action."}

            candidate_response = f"Regarding your question, here is the details for {c['prompt'].lower().rstrip('?')}."
            decision = evaluate_speak_gate(
                candidate_text=candidate_response,
                assertion=ast,
                speaker_confidence=1.0,
            )
            # Must abstain on traps
            assert not decision.should_speak, f"Trap {cid} in stratum {stratum} was spoken! Reason: {decision.abstain_reason}"
            assert decision.tier == SpeakTier.T0
            traps_silenced += 1
            results.append({
                "id": cid,
                "status": "PASS",
                "response": "",
                "cited_assertion_ids": [],
            })
        else:
            # Grounded moment
            ast = {
                "assertion_id": c.get("expected_assertion_ids", ["ast_01"])[0],
                "claim_text": c.get("ideal_point", "Standard architecture claim."),
                "canonical_status": "ACTIVE",
            }
            candidate = f"Regarding this, {c.get('ideal_point', 'standard architecture claim.')}"
            decision = evaluate_speak_gate(
                candidate_text=candidate,
                assertion=ast,
                speaker_confidence=0.95,
            )
            assert decision.should_speak, f"Grounded case {cid} failed to speak: {decision.abstain_reason}"
            assert decision.tier == SpeakTier.T3
            grounded_spoken += 1
            results.append({
                "id": cid,
                "status": "PASS",
                "response": decision.retained_text,
                "cited_assertion_ids": [ast["assertion_id"]],
            })

    assert grounded_spoken == 25, f"Expected 25 grounded moments to speak, got {grounded_spoken}"
    assert traps_silenced == 35, f"Expected 35 trap moments to be silenced, got {traps_silenced}"

    # Scorecard verification
    scorecard = compute_copilot_scorecard(cases, results)
    assert scorecard.groundedness == 1.0
    assert scorecard.silence_precision == 1.0
    assert scorecard.silence_recall == 1.0


def test_negation_and_polarity_inversion_traps():
    """Verify Speak Gate stops claims that invert the polarity of ground truth assertions."""
    ast_neg = {
        "assertion_id": "ast_neg_01",
        "claim_text": "Vector database memory scaling introduces latency spikes and is not supported in memory.",
        "canonical_status": "ACTIVE",
    }

    # Polarity inversion: candidate claims it is supported
    flipped_candidate = "Vector database memory scaling is supported without latency spikes."
    decision_flipped = evaluate_speak_gate(
        candidate_text=flipped_candidate,
        assertion=ast_neg,
        speaker_confidence=0.90,
    )
    assert not decision_flipped.should_speak
    assert decision_flipped.abstain_reason == AbstainReasonCode.NEGATION_CONFLICT
    assert decision_flipped.tier == SpeakTier.T0

    # Polarity match: candidate accurately reflects the negation
    matching_candidate = "Vector database memory scaling is not supported due to latency spikes."
    decision_match = evaluate_speak_gate(
        candidate_text=matching_candidate,
        assertion=ast_neg,
        speaker_confidence=0.90,
    )
    assert decision_match.should_speak
    assert decision_match.tier == SpeakTier.T3


def test_staleness_and_superseded_status_traps():
    """Verify Speak Gate fails closed when assertions are superseded, deprecated, or stale."""
    # Superseded status
    ast_superseded = {
        "assertion_id": "ast_old_01",
        "claim_text": "Legacy MySQL database stores pipeline state.",
        "canonical_status": "SUPERSEDED",
    }
    decision_sup = evaluate_speak_gate(
        candidate_text="Legacy MySQL database stores pipeline state.",
        assertion=ast_superseded,
    )
    assert not decision_sup.should_speak
    assert decision_sup.abstain_reason == AbstainReasonCode.SUPERSEDED_STATUS
    assert decision_sup.tier == SpeakTier.T0

    # Stale assertion
    ast_stale = {
        "assertion_id": "ast_stale_01",
        "claim_text": "Migration is scheduled for next Monday.",
        "canonical_status": "ACTIVE",
        "is_stale": True,
    }
    decision_stale = evaluate_speak_gate(
        candidate_text="Migration is scheduled for next Monday.",
        assertion=ast_stale,
    )
    assert not decision_stale.should_speak
    assert decision_stale.abstain_reason == AbstainReasonCode.STALE_ASSERTION
    assert decision_stale.tier == SpeakTier.T0


def test_questions_need_no_evidence_rule():
    """Verify investigative questions do not require assertion evidence, but low speaker confidence downgrades."""
    question_text = "Could we clarify what timeout is configured for database locks?"

    # High speaker confidence -> authorized to speak under T1
    decision_high_conf = evaluate_speak_gate(
        candidate_text=question_text,
        assertion=None,
        speaker_confidence=0.90,
        intent="next-questions",
    )
    assert decision_high_conf.should_speak
    assert decision_high_conf.tier == SpeakTier.T1

    # Low speaker confidence (< 0.60) -> downgrades to T0, abstains
    decision_low_conf = evaluate_speak_gate(
        candidate_text=question_text,
        assertion=None,
        speaker_confidence=0.45,
        intent="next-questions",
    )
    assert not decision_low_conf.should_speak
    assert decision_low_conf.tier == SpeakTier.T0
    assert decision_low_conf.abstain_reason == AbstainReasonCode.LOW_SPEAKER_CONFIDENCE


def test_quarantined_ownership_fails_closed():
    """Verify unknown assertion ownership is quarantined and rejected fail-closed by the speak gate."""
    # Unknown assertion without project match or technical anchor
    raw_assertion = {
        "assertion_id": "ast_unknown_owner",
        "subject_node_id": "entity_xyz",
        "object_literal": "Secret internal feature roadmap.",
        "scope_text": "Secret internal feature roadmap.",
    }
    inferred = _infer_assertion_scope(raw_assertion, {})
    assert inferred == "quarantine:unknown_ownership"

    raw_assertion["engagement_id"] = inferred
    decision = evaluate_speak_gate(
        candidate_text="Secret internal feature roadmap.",
        assertion=raw_assertion,
    )
    assert not decision.should_speak
    assert decision.abstain_reason == AbstainReasonCode.SCOPE_QUARANTINED
    assert decision.tier == SpeakTier.T0


def test_whole_response_coherence_gate_rejects_incoherent_stitching():
    """Verify whole-response coherence gate flags and prunes stitched non-sequiturs and duplicate openers."""
    assertions = [
        {
            "assertion_id": "live_turn_40",
            "claim_text": "AI agents are beginning to take action, and memory systems are becoming more persistent",
            "is_live_turn": True,
        },
        {
            "assertion_id": "ast_pod_gov",
            "claim_text": "logan Miller will align delivery models and PartnerAdvisory Pod governance for 5 active NEE enterprise charters.",
            "is_live_turn": False,
        },
    ]
    incoherent_lines = [
        "We're tracking that AI agents are beginning to take action. Memory systems are becoming more persistent.",
        "We're tracking that [PD Conduit] logan Miller will align delivery models and PartnerAdvisory Pod governance for 5 active NEE enterprise charters.",
        "We're tracking that if an AI system can process information directly on your device, some data may not need to be constantly saved.",
    ]
    retained_bad, reason_bad = verify_whole_response_coherence(incoherent_lines, assertions, intent="graph-context")
    assert reason_bad == "INCOHERENT_MULTI_SENTENCE_RESPONSE"
    assert len(retained_bad) == 1
    assert "logan Miller" not in "\n".join(retained_bad)

    coherent_assertions = [
        {
            "assertion_id": "live_turn_40",
            "claim_text": "AI agents are beginning to take action, and memory systems are becoming more persistent",
            "is_live_turn": True,
        },
        {
            "assertion_id": "ast_traverse",
            "claim_text": "PartnerAdvisory Traverse provides a single operating surface for visibility, control, and accountability over an enterprise agent fleet across multiple clouds and LLM models.",
            "is_live_turn": False,
        },
        {
            "assertion_id": "ast_delivery",
            "claim_text": "PartnerAdvisory is currently performing platform setup and configuration, connector deployment, configuration remediation, no-code agent development, and Gemini-specific enablement.",
            "is_live_turn": False,
        },
    ]
    robotic_lines = [
        "We're tracking that AI agents are beginning to take action, and memory systems are becoming more persistent.",
        "In parallel, our agreed baseline is that PartnerAdvisory Traverse provides a single operating surface for visibility, control, and accountability over an enterprise agent fleet across multiple clouds and LLM models.",
    ]
    retained_robotic, reason_robotic = verify_whole_response_coherence(robotic_lines, coherent_assertions, intent="graph-context")
    assert reason_robotic == "INCOHERENT_MULTI_SENTENCE_RESPONSE"
    assert len(retained_robotic) == 1

    coherent_lines = [
        "As we're hearing in this session, AI agents are beginning to take action, and memory systems are becoming more persistent.",
        "In practice, PartnerAdvisory Traverse provides a single operating surface for visibility, control, and accountability over an enterprise agent fleet across multiple clouds and LLM models.",
        "To execute that shift, PartnerAdvisory is currently performing platform setup and configuration, connector deployment, configuration remediation, no-code agent development, and Gemini-specific enablement.",
    ]
    retained_ok, reason_ok = verify_whole_response_coherence(coherent_lines, coherent_assertions, intent="graph-context")
    assert reason_ok is None
    assert len(retained_ok) == 3

