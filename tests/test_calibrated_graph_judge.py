"""
tests/test_calibrated_graph_judge.py

Unit test suite for Wave 4:
- Two-Tier Evaluation Architecture Formalization (Model A)
- Meeting Archetype Awareness (TECHNICAL_DEMONSTRATION vs STRATEGIC_EXECUTIVE)
- Discrete Deduction Rubric Calibration & Composite Score Arithmetic
- Golden Fixture Qualification (Output 03 >= 95 PASS)
- Release Threshold Policy Integration
"""
from __future__ import annotations

import json
from typing import Any, Dict

from dual_pipeline.llm_judge_core.enterprise import EnterpriseGraphReviewJudge
from dual_pipeline.llm_judge_core.graph import (
    GraphExtractionJudge,
    _assign_graph_verdict,
    _detect_meeting_archetype,
)

from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY, ReleaseVerdict


def _get_golden_technical_fixture() -> Dict[str, Any]:
    """Certified golden fixture of 09/11 LENS Technical Demonstration."""
    return {
        "schema_version": "5.7",
        "meeting_id": "lens-phase1-demo-20260911",
        "meeting_archetype": "TECHNICAL_DEMONSTRATION",
        "classification": "internal",
        "audiences": ["nee-engineering", "partner_advisory-delivery"],
        "evidence_index": {
            "stmt-01": {
                "statement_id": "stmt-01",
                "text": "Apex Global business users reported Dev application errors preventing rule authoring at [04:15].",
                "exact_text": "Apex Global business users reported Dev application errors preventing rule authoring at [04:15].",
                "speaker": "harper Hoffman",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "04:15"},
            },
            "stmt-02": {
                "statement_id": "stmt-02",
                "text": "PartnerAdvisory will present a bounded Phase 1 stabilization estimate on September 14 2026.",
                "exact_text": "PartnerAdvisory will present a bounded Phase 1 stabilization estimate on September 14 2026.",
                "speaker": "Amit Ayer",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "07:22"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "workstream:lens_phase_1",
                    "type": "workstream",
                    "name": "LENS Phase 1",
                    "mutation": "CONFIRM",
                },
                {
                    "id": "system:rule_creation_engine",
                    "type": "system",
                    "name": "Rule Creation Engine",
                    "mutation": "CONFIRM",
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-01",
                    "from_id": "system:rule_creation_engine",
                    "to_id": "workstream:lens_phase_1",
                    "relationship": "VALIDATES",
                    "evidence_ids": ["stmt-01"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:stabilization_hold",
                    "status": "APPROVED",
                    "observed_at": "09/11/2026",
                    "decision_question": "Shall Phase II proceed under conditional hold pending stabilization?",
                    "rationale": "Alignment on Phase 1 defect resolution prior to contract expansion.",
                    "evidence_ids": ["stmt-02"],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [
                {
                    "risk_id": "risk:dev_defect_latency",
                    "status": "ACTIVE",
                    "observed_at": "09/11/2026",
                    "description": "Unresolved Dev errors prevent business users from authoring extraction rules.",
                    "evidence_ids": ["stmt-01"],
                }
            ],
        },
        "evidence_only_statement_ids": [],
    }


# ── Archetype Awareness Tests ───────────────────────────────────────────────────


def test_archetype_explicit_override() -> None:
    """Verify explicit archetype parameter overrides metadata and content heuristics."""
    f3_data = {"meeting_archetype": "OPERATIONAL_DISCOVERY"}
    archetype = _detect_meeting_archetype(f3_data, "Screen share demonstration", explicit="TECHNICAL_DEMONSTRATION")
    assert archetype == "TECHNICAL_DEMONSTRATION"


def test_archetype_metadata_detection() -> None:
    """Verify archetype is extracted from payload metadata when explicit is None."""
    f3_data = {"meeting_archetype": "STRATEGIC_EXECUTIVE"}
    archetype = _detect_meeting_archetype(f3_data, "Discussion of sprint tasks", explicit=None)
    assert archetype == "STRATEGIC_EXECUTIVE"


def test_archetype_heuristic_detection() -> None:
    """Verify content heuristics detect technical demonstrations from transcript cues."""
    f3_data = {}
    archetype = _detect_meeting_archetype(
        f3_data,
        "Here is the rule authoring screen share and dev walkthrough of the API.",
        explicit=None,
    )
    assert archetype == "TECHNICAL_DEMONSTRATION"


# ── Two-Tier Evaluation Architecture Tests (Model A) ───────────────────────────


def test_two_tier_evaluation_model_a_separation() -> None:
    """Verify Tier 1 (GraphExtractionJudge) and Tier 2 (EnterpriseGraphReviewJudge) are cleanly decoupled."""
    fixture = _get_golden_technical_fixture()
    f3_str = json.dumps(fixture)
    f1_str = "harper Hoffman demonstrated the Rule Creation Engine screen share on 09/11/2026."

    # Tier 1: Ingestion Mutation Gate
    tier1_judge = GraphExtractionJudge(mock=True)
    tier1_result = tier1_judge.evaluate(f3_str, f1_str, archetype="TECHNICAL_DEMONSTRATION")

    assert tier1_result["verdict"] == "PASS"
    assert tier1_result["composite_score"] >= CANONICAL_RELEASE_POLICY.output3_pass_threshold
    assert "dimension_1_strategic_edge" in tier1_result
    assert "dimension_6_evidence_grounding_and_schema" in tier1_result

    # Tier 2: Post-Activation Strategic Architecture Audit
    tier2_judge = EnterpriseGraphReviewJudge(mock=True)
    tier2_result = tier2_judge.evaluate(fixture, context_text=f1_str)

    assert tier2_result["canonical_mutation"] is False
    assert tier2_result["schema_version"] == "enterprise-graph-review-1.0"
    assert "maturity_band" in tier2_result
    assert "dimension_1_business_capability_depth" in tier2_result["dimension_scores"]
    assert "dimension_10_multi_client_federation_scalability" in tier2_result["dimension_scores"]


# ── Discrete Rubric Arithmetic & Verdict Assignment Tests ───────────────────────


def test_discrete_composite_score_recalculation() -> None:
    """Verify Python runtime recalculates composite score as exact sum of 6 discrete dimensions."""
    raw_eval = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 14,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 13,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 18,
        "composite_score": 50,  # Deliberately divergent raw score
    }

    assigned = _assign_graph_verdict(raw_eval)
    # Sum: 15 + 14 + 20 + 13 + 15 + 18 = 95
    assert assigned["composite_score"] == 95
    assert assigned["verdict"] == "PASS"


def test_release_threshold_policy_verdict_boundaries() -> None:
    """Verify verdicts strictly track ReleaseThresholdPolicy boundaries for Output 3 (>=90 PASS, <90 FAIL)."""
    # 90 -> PASS
    res_pass = _assign_graph_verdict({"composite_score": 90})
    assert res_pass["verdict"] == ReleaseVerdict.PASS.value

    # 89 -> FAIL (Output 3 strictly forbids PASS_ADVISORY)
    res_adv = _assign_graph_verdict({"composite_score": 89})
    assert res_adv["verdict"] == ReleaseVerdict.FAIL.value

    # 80 -> FAIL
    res_floor = _assign_graph_verdict({"composite_score": 80})
    assert res_floor["verdict"] == ReleaseVerdict.FAIL.value

    # 79 -> FAIL
    res_fail = _assign_graph_verdict({"composite_score": 79})
    assert res_fail["verdict"] == ReleaseVerdict.FAIL.value


def test_c19_career_firewall_quarantine_overrides_score() -> None:
    """Verify Rule C-19 leak immediately quarantines candidate with FAIL verdict."""
    judge = GraphExtractionJudge(mock=True)
    leaked_fixture = _get_golden_technical_fixture()
    leaked_fixture["graph_mutations"]["decision_updates"][0]["rationale"] = (
        "Secures $1.2M executive trajectory for Amit Ayer."
    )

    result = judge.evaluate(json.dumps(leaked_fixture), "Transcript reference text")
    assert result["verdict"] == "FAIL"
    assert result["composite_score"] <= 30
    assert "RULE C-19 VIOLATION" in result["forensic_rationale"]


# ── Golden Fixture Technical Demonstration Qualification (>= 95) ───────────────


def test_golden_technical_demonstration_achieves_gte_95() -> None:
    """Verify certified 09/11 technical demonstration achieves composite score >= 95 PASS."""
    fixture = _get_golden_technical_fixture()
    judge = GraphExtractionJudge(mock=True)

    result = judge.evaluate(
        json.dumps(fixture),
        f1_text="Rule Creation Engine walkthrough screen share.",
        archetype="TECHNICAL_DEMONSTRATION",
    )

    assert result["verdict"] == "PASS"
    assert result["composite_score"] >= 95
    assert result["meeting_archetype"] == "TECHNICAL_DEMONSTRATION"
    assert result["dimension_1_strategic_edge"] == 15
    assert result["dimension_5_exclusion_of_action_items"] == 15
