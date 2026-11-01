"""Wave 4 tests: Grounding Verifier & NEGATION_CONFLICT False-Positive Remediation."""
from __future__ import annotations

import argparse
from typing import Any, Dict, List

from dual_pipeline.cli.copilot_speech import _normalize_spoken_ear, render_speaker_comments
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _filter_body_through_speak_gate,
    _render_and_validate_body,
)
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    evaluate_speak_gate,
    has_negation_flip,
)
from dual_pipeline.live_meeting_recorder_core.proactive_inflection_agent import (
    compress_for_speak_gate,
)


class _DummyRetriever:
    def __init__(self) -> None:
        self.engagement_id = "lens"
        self._topology = type("Topo", (), {"nodes": {}, "assertions": {}})()
        self._last_gap_conflicts = []


def _generate_251_lens_assertions() -> List[Dict[str, Any]]:
    """Generates the 251 multi-clause, contraction, and Contract No. LENS assertion patterns."""
    patterns = [
        "Ed More confirmed the model will not block field operators; the 2,500 production images are live.",
        "harper Hoffman noted we do not have full visibility into the rolling queue; the golden dataset covers 200 images.",
        "Under Contract No. 4600027256, PartnerAdvisory delivers SOW 1 milestones for LENS pole inspection.",
        "Michael Reiss cannot validate the self-learning loop until historical training batches are exported.",
        "Operators don't see the 2,500 background images because the UI filter defaults to the last 200 cards.",
        "Rule authoring mistakes are not model defects; prompt rules need a dedicated governance review.",
        "The team hasn't finalized the 1,800 equipment mappings; 50 primary types are currently active in production.",
        "Chaffee Burke and Rob Koch will transition architecture oversight; this does not delay the Thursday sprint.",
    ]
    assertions: List[Dict[str, Any]] = []
    for idx in range(251):
        base = patterns[idx % len(patterns)]
        claim = f"{base} (Batch ref {idx + 1})"
        assertions.append(
            {
                "assertion_id": f"asrt_lens_{idx:03d}",
                "subject_node_id": "workstream:lens",
                "predicate": "HAS_STATUS",
                "object_node_id": f"node:status_{idx:03d}",
                "claim_text": claim,
                "object_literal": claim,
                "verbatim_span": base[:45],
                "canonical_status": "ACTIVE",
                "confidence_score": 0.94,
                "is_primary": True,
                "provenance_scope": "PRIMARY",
            }
        )
    return assertions


def test_251_lens_assertions_zero_false_positive_negation_conflict() -> None:
    assertions = _generate_251_lens_assertions()
    assert len(assertions) == 251
    false_positives = 0
    for ast in assertions:
        raw_text = ast["claim_text"]
        rendered = _normalize_spoken_ear(raw_text)
        if has_negation_flip(rendered, raw_text):
            false_positives += 1
        gate = evaluate_speak_gate(rendered, assertion=ast, max_words=None)
        if gate.abstain_reason == AbstainReasonCode.NEGATION_CONFLICT:
            false_positives += 1
    assert false_positives == 0


def test_contraction_and_contract_no_normalization_and_true_flip() -> None:
    # Contraction equivalence: "do not" -> "don't", "cannot" -> "can't", "will not" -> "won't"
    assert not has_negation_flip(
        "Mandy, we don't have full queue visibility.",
        "Mandy noted we do not have full queue visibility.",
    )
    assert not has_negation_flip(
        "Ed, operators can't see the 2500 images.",
        "Operators cannot see the 2500 images.",
    )
    assert not has_negation_flip(
        "Contract No. 4600027256 is active in production.",
        "Under Contract No. 4600027256, the deployment is active in production.",
    )
    # True negation flip still caught fail-closed
    assert has_negation_flip(
        "The model will not block field operators in production.",
        "The model will block field operators in production.",
    )


def test_candidate_backtracking_recovers_when_early_candidates_fail() -> None:
    retriever = _DummyRetriever()
    # First 3 candidates are malformed or superseded; candidates 4..6 are valid
    candidates = [
        {"assertion_id": "bad_1", "claim_text": "", "object_literal": "", "is_primary": True},
        {"assertion_id": "bad_2", "claim_text": "```code```", "object_literal": "```code```", "is_primary": True},
        {
            "assertion_id": "bad_3",
            "claim_text": "Superseded old status from prior quarter.",
            "object_literal": "Superseded old status from prior quarter.",
            "canonical_status": "SUPERSEDED",
            "is_primary": True,
        },
        {
            "assertion_id": "good_4",
            "claim_text": "Ed More confirmed 2500 production images are live.",
            "object_literal": "Ed More confirmed 2500 production images are live.",
            "canonical_status": "ACTIVE",
            "is_primary": True,
        },
        {
            "assertion_id": "good_5",
            "claim_text": "Michael Reiss is auditing the golden dataset with data science.",
            "object_literal": "Michael Reiss is auditing the golden dataset with data science.",
            "canonical_status": "ACTIVE",
            "is_primary": True,
        },
    ]
    args = argparse.Namespace(words=None, _parsed_requested_count=2)
    body, fail_code = _render_and_validate_body(
        "gaps", "Mandy", candidates, args, retriever, selected_utterance=None
    )
    assert fail_code == ""
    assert "2500 production images" in body
    assert "Michael Reiss" in body


def test_compress_for_speak_gate_preserves_clause_and_strips_dangling_words() -> None:
    raw = (
        "Based on the historical record, harper Hoffman noted the golden dataset covers 200 images; "
        "furthermore the team is reviewing additional pole asset categories and because of the"
    )
    compressed = compress_for_speak_gate(raw, max_words=18)
    assert len(compressed.split()) <= 18
    assert not compressed.lower().rstrip(".").endswith(("and", "because", "of", "the"))
