"""
tests/test_calibrated_graph_judge_floors.py

Verifies dimension-aware floor evaluation in Output 3 GraphExtractionJudge.
15-point dimensions enforce floor=11 (73.3%), 20-point dimensions enforce floor=14 (70%).
"""
from __future__ import annotations

from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict


def test_graph_verdict_passes_with_13_on_15_point_dimension():
    # Dimension 1 = 13/15 (86.7%), Dimension 2 = 12/15, Composite = 91 >= 90
    payload = {
        "dimension_1_strategic_edge": 13,
        "dimension_2_knowledge_layer_density": 12,
        "dimension_3_semantic_atomicity": 19,
        "dimension_4_predicate_specificity": 14,
        "dimension_5_exclusion_of_action_items": 14,
        "dimension_6_evidence_grounding_and_schema": 19,
        "forensic_rationale": "High quality extraction",
    }
    res = _assign_graph_verdict(payload)
    assert res["composite_score"] == 91
    assert res["verdict"] == "PASS"
    assert "floor failure" not in res.get("forensic_rationale", "").lower()


def test_graph_verdict_fails_when_below_15_point_floor():
    # Dimension 1 = 10 (< 11 floor)
    payload = {
        "dimension_1_strategic_edge": 10,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
        "forensic_rationale": "",
    }
    res = _assign_graph_verdict(payload)
    assert res["verdict"] == "FAIL"
    assert "floor=11" in res["forensic_rationale"]


def test_graph_verdict_fails_when_below_20_point_floor():
    # Dimension 3 = 13 (< 14 floor)
    payload = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 13,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
        "forensic_rationale": "",
    }
    res = _assign_graph_verdict(payload)
    assert res["verdict"] == "FAIL"
    assert "floor=14" in res["forensic_rationale"]
