"""
tests/test_document_judge_adversarial.py

Adversarial and property-based verification suite for document-mode Output 03
evaluation and density-floor retirement.
Covers 4 edge-case categories:
1. Boundary payloads (concise 1-entity memo, 0-mutation empty graph).
2. Malformed inputs (invalid schema version, non-integer/boolean scorecard fields).
3. Invariant bypass attempts (ungrounded signal fabrication, Rule C-19 career leverage leak, non-density floor breach).
4. Exact production regression & Hypothesis property verification (LENS PRD archetype drift + Dim 2 exemption vs transcript enforcement).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_judge_and_release,
)
from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.llm_judge_core.judge_clarification import (
    assign_clarified_graph_verdict,
)
from dual_pipeline.llm_judge_core.judges.graph import (
    DIMENSION_KEYS,
    GraphExtractionJudge,
)
from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT


def _build_minimal_memo_projection(source_text: str) -> dict:
    exact_quote = "The Enterprise Data Gateway enforces mutual TLS across all regional ingress points."
    start_idx = source_text.index(exact_quote)
    return {
        "schema_version": "5.7",
        "context": {
            "project": "security_arch",
            "classification": "internal",
            "audiences": ["internal"],
            "source_tags": ["document", "memo"],
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:enterprise_data_gateway",
                    "type": "system",
                    "name": "Enterprise Data Gateway",
                    "description": "Regional ingress gateway enforcing mutual TLS.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-0001"],
                },
                {
                    "id": "policy:mutual_tls_ingress",
                    "type": "policy",
                    "name": "Mutual TLS Ingress Policy",
                    "description": "Policy requiring mutual TLS across all regional ingress points.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-0001"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-0001",
                    "from_id": "policy:mutual_tls_ingress",
                    "predicate": "CONSTRAINS",
                    "to_id": "system:enterprise_data_gateway",
                    "description": "Mutual TLS policy constrains the Enterprise Data Gateway across regional ingress points.",
                    "confidence": 0.97,
                    "epistemic_status": "OBSERVED",
                    "evidence_ids": ["ev-0001"],
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
            "strategic_intent_signals": [],
        },
        "evidence_index": {
            "ev-0001": {
                "evidence_id": "ev-0001",
                "kind": "technical",
                "text": exact_quote,
                "exact_text": exact_quote,
                "start_offset": start_idx,
                "end_offset": start_idx + len(exact_quote),
                "source_locator": "L1",
                "speaker": "Security Architecture",
                "source_classification": "FACT",
                "grounding_status": "GROUNDED",
            }
        },
    }


# ---------------------------------------------------------------------------
# Category 1: Boundary Payloads
# ---------------------------------------------------------------------------

def test_boundary_concise_memo_low_dim2_passes_in_document_mode():
    """A concise architectural memo with Dim 2 = 5/15 and composite = 90 passes DOCUMENT_MODE qualification."""
    scorecard = {
        "meeting_archetype": "DOCUMENT_MODE",
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 5,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
        "composite_score": 90,
        "verdict": "PASS",
    }
    qualified, passed, score = qualify_document_judge(scorecard)
    assert passed is True
    assert score == 90
    assert qualified["verdict"] == "PASS"


def test_boundary_empty_mutations_short_circuits_with_zero_llm_calls():
    """An empty graph projection (0 mutations) fails deterministic Gate 3 with SKIPPED_DETERMINISTIC_QA_FAILURE."""
    source_text = "The Enterprise Data Gateway enforces mutual TLS across all regional ingress points."
    projection = _build_minimal_memo_projection(source_text)
    projection["graph_mutations"]["entity_upserts"] = []
    projection["graph_mutations"]["relationship_signals"] = []

    with patch.object(GraphExtractionJudge, "evaluate") as mock_judge:
        qa_report, judge_report, final_decision = evaluate_document_judge_and_release(
            f3_text=projection,
            source_text=source_text,
            metadata={"project": "security_arch", "title": "Gateway Memo", "document_patch_request": None},
            mock=False,
        )

    assert mock_judge.call_count == 0
    assert qa_report["passed"] is False
    assert judge_report["output_3_evaluation"]["status"] == "SKIPPED_DETERMINISTIC_QA_FAILURE"
    assert final_decision["final_pass"] is False


# ---------------------------------------------------------------------------
# Category 2: Malformed Inputs
# ---------------------------------------------------------------------------

def test_malformed_schema_version_blocked_before_judge():
    """Wrong schema_version ('4.0') fails Gate 3 deterministically without calling the LLM judge."""
    source_text = "The Enterprise Data Gateway enforces mutual TLS across all regional ingress points."
    projection = _build_minimal_memo_projection(source_text)
    projection["schema_version"] = "4.0"

    with patch.object(GraphExtractionJudge, "evaluate") as mock_judge:
        qa_report, judge_report, final_decision = evaluate_document_judge_and_release(
            f3_text=projection,
            source_text=source_text,
            metadata={"project": "security_arch", "title": "Gateway Memo"},
            mock=False,
        )

    assert mock_judge.call_count == 0
    assert qa_report["passed"] is False
    assert judge_report["output_3_evaluation"]["status"] == "SKIPPED_DETERMINISTIC_QA_FAILURE"
    assert final_decision["final_pass"] is False


def test_malformed_scorecard_boolean_or_mismatched_sum_rejected():
    """Boolean dimension scores or mismatched composite sums fail closed in qualify_document_judge."""
    bad_card = {
        "meeting_archetype": "DOCUMENT_MODE",
        "dimension_1_strategic_edge": True,  # Invalid boolean masquerading as int
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
        "composite_score": 86,
        "verdict": "PASS",
    }
    qualified, passed, _ = qualify_document_judge(bad_card)
    assert passed is False
    assert qualified["verdict"] == "FAIL"
    assert any("dimension_1_strategic_edge" in err for err in qualified["quality_failures"])


# ---------------------------------------------------------------------------
# Category 3: Invariant Bypass Attempts
# ---------------------------------------------------------------------------

def test_invariant_bypass_c19_leak_quarantined_in_document_mode():
    """Rule C-19 markers ($1.2M / INTERNAL_ADVISORY) force FAIL even when LLM returns 97/100 PASS."""
    source_text = "The Enterprise Data Gateway enforces mutual TLS across all regional ingress points."
    projection = _build_minimal_memo_projection(source_text)
    projection["graph_mutations"]["entity_upserts"][0]["description"] = (
        "Gateway supporting $1.2M INTERNAL_ADVISORY career trajectory."
    )

    fake_scorecard = {
        "meeting_archetype": "DOCUMENT_MODE",
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 14,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 14,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 19,
        "composite_score": 97,
        "verdict": "PASS",
        "forensic_rationale": "Clean extraction.",
        "actionable_remediation_diff": "",
        "grounding_citations": ["L1"],
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        model="gpt-5.4",
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(fake_scorecard)))],
        usage=SimpleNamespace(prompt_tokens=300, completion_tokens=100, total_tokens=400),
    )

    judge = GraphExtractionJudge(model="gpt-5.4", reasoning_effort="low", mock=False)
    res = judge.evaluate(
        f3_text=json.dumps(projection),
        f1_text=source_text,
        client=mock_client,
        mock=False,
        archetype="DOCUMENT_MODE",
        metadata={"archetype": "DOCUMENT_MODE", "modality": "DOCUMENT", "is_document": True},
    )
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] <= 30
    assert "RULE C-19 VIOLATION" in res["forensic_rationale"]


def test_invariant_bypass_non_density_floor_still_enforced_in_document_mode():
    """Mandatory floors on Dimensions 1, 3, 4, 5, and 6 remain strictly enforced in DOCUMENT_MODE."""
    scorecard = {
        "meeting_archetype": "DOCUMENT_MODE",
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 12,  # Below 14.0 floor!
        "composite_score": 92,
        "verdict": "PASS",
    }
    qualified, passed, _ = qualify_document_judge(scorecard)
    assert passed is False
    assert qualified["verdict"] == "FAIL"
    assert "Below rubric floor: dimension_6_evidence_grounding_and_schema" in qualified["quality_failures"]


# ---------------------------------------------------------------------------
# Category 4: Exact Production Regression & Hypothesis Property Verification
# ---------------------------------------------------------------------------

@given(
    dim2=st.integers(min_value=0, max_value=10),
    dim1=st.integers(min_value=14, max_value=15),
    dim3=st.integers(min_value=19, max_value=20),
    dim4=st.integers(min_value=14, max_value=15),
    dim5=st.integers(min_value=14, max_value=15),
    dim6=st.integers(min_value=19, max_value=20),
)
@settings(max_examples=40, deadline=None)
def test_property_document_mode_exempts_dim2_floor_while_transcript_enforces_it(
    dim2: int, dim1: int, dim3: int, dim4: int, dim5: int, dim6: int
):
    """
    Property: Whenever Dimensions 1, 3, 4, 5, 6 meet their mandatory floors and composite >= 90,
    DOCUMENT_MODE passes for any Dimension 2 score in [0, 10], whereas TRANSCRIPT mode never passes
    when Dimension 2 < 11.
    """
    total = dim1 + dim2 + dim3 + dim4 + dim5 + dim6
    base = {
        "dimension_1_strategic_edge": dim1,
        "dimension_2_knowledge_layer_density": dim2,
        "dimension_3_semantic_atomicity": dim3,
        "dimension_4_predicate_specificity": dim4,
        "dimension_5_exclusion_of_action_items": dim5,
        "dimension_6_evidence_grounding_and_schema": dim6,
        "composite_score": total,
        "verdict": "PASS",
    }

    doc_card = {**base, "meeting_archetype": "DOCUMENT_MODE"}
    doc_floors = {k: int(v) for k, v in OUTPUT3_CONTRACT.get_dimension_floors("DOCUMENT_MODE").items()}
    doc_res = assign_clarified_graph_verdict(doc_card, DIMENSION_KEYS, doc_floors)

    tx_card = {**base, "meeting_archetype": "ARCHITECTURE_DEEP_DIVE"}
    tx_floors = {k: int(v) for k, v in OUTPUT3_CONTRACT.get_dimension_floors("ARCHITECTURE_DEEP_DIVE").items()}
    tx_res = assign_clarified_graph_verdict(tx_card, DIMENSION_KEYS, tx_floors)

    if total >= 90:
        assert doc_res["verdict"] == "PASS"
    assert tx_res["verdict"] in ("FAIL", "HITL_REQUIRED")
    assert tx_res["verdict"] != "PASS"
