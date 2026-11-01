"""
Tests for Hardened Career Leverage Firewall & Multi-Judge Architecture:
- AgenticLeverageJudge ($1.2M SVP Gatekeeper)
- EnterpriseGraphReviewJudge (10 Strategic Dimensions / $1.2M Benchmark)
- Rule C-19 Career Leverage Firewall Fail-Closed Enforcement
- Restricted Advisory Enclave Projection Decoupling
"""

import json
from pathlib import Path

from dual_pipeline.llm_judge import (
    AgenticLeverageJudge,
    CrossArtifactReconciler,
    EnterpriseGraphReviewJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
    JudgeAgenticLeverage,
)
from dual_pipeline.transcript_stages import (
    INTERNAL_ADVISORY_NAMESPACE,
    AgenticLeverageNode,
    RestrictedAdvisoryProjection,
)


def test_agentic_leverage_judge_gatekeeper_pass():
    judge = AgenticLeverageJudge(mock=True)
    valid_node = AgenticLeverageNode(
        node_type="CAREER_VISIBILITY",
        key_players=["Tanner (VP Cloud)", "Drew Oakley (Managing Director)"],
        political_rationale="Tanner is quietly concerned about NERC compliance audit delays but hasn't escalated to the CIO yet.",
        networking_wedge="Schedule 15-min sync with Tanner to brief him on our pre-tested grid compliance blueprint before the formal review.",
        verbatim_quote="We might slip on the audit timeline if the cloud connector isn't certified by October.",
        speaker="Tanner",
    )
    result = judge.evaluate_agentic_leverage(valid_node, transcript_text="test transcript", mock=True)
    assert isinstance(result, JudgeAgenticLeverage)
    assert result.pass_gate is True
    assert result.networking_target_identified is True
    assert result.is_actionable_wedge is True
    assert result.political_materiality >= 8
    assert result.rejection_reason is None


def test_agentic_leverage_judge_rejection_target_test():
    judge = AgenticLeverageJudge(mock=True)
    # Lacks senior executive target title
    junior_node = AgenticLeverageNode(
        node_type="INFO_ASYMMETRY",
        key_players=["intern"],
        political_rationale="The intern does not know how git works and needs help.",
        networking_wedge="Show the intern how to push branches to look helpful.",
        verbatim_quote="I don't know how git works.",
        speaker="intern",
    )
    result = judge.evaluate_agentic_leverage(junior_node, transcript_text="test transcript", mock=True)
    assert result.pass_gate is False
    assert result.networking_target_identified is False
    assert "Target Test" in str(result.rejection_reason)


def test_agentic_leverage_judge_evaluate_collection():
    judge = AgenticLeverageJudge(mock=True)
    node1 = AgenticLeverageNode(
        node_type="CAREER_VISIBILITY",
        key_players=["VP Cloud Engineering"],
        political_rationale="Detailed strategic rationale exposing latent organizational friction between infra and apps.",
        networking_wedge="Connect with VP Cloud to offer an independent cloud governance readiness assessment.",
        verbatim_quote="Infra and apps are not aligned on the landing zone.",
        speaker="VP Cloud",
    )
    scorecards = judge.evaluate_agentic_nodes([node1], mock=True)
    assert len(scorecards) == 1
    assert scorecards[0]["judgment"]["pass_gate"] is True
    assert scorecards[0]["node"]["node_type"] == "CAREER_VISIBILITY"


def test_enterprise_graph_review_judge_10_dimensions():
    judge = EnterpriseGraphReviewJudge(mock=True)
    canonical_graph = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:grid-platform", "type": "system", "mutation": "CONFIRM"},
                {"id": "org:partner_advisory", "type": "org", "mutation": "CONFIRM"},
                {"id": "person:dan-okray", "type": "person", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                {
                    "from_id": "org:partner_advisory",
                    "to_id": "system:grid-platform",
                    "relationship": "GOVERNS",
                    "signal_kind": "FACT",
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
    }
    review = judge.evaluate(canonical_graph, context_text="Context text", mock=True)
    assert review["schema_version"] == "enterprise-graph-review-1.0"
    assert review["canonical_mutation"] is False
    assert "evaluated_at" in review
    assert review["composite_score"] >= 80
    assert "maturity_band" in review
    scores = review["dimension_scores"]
    assert len(scores) == 10
    assert "dimension_1_business_capability_depth" in scores
    assert "dimension_7_bi_temporal_rigor_provider_free" in scores
    assert "dimension_9_board_level_synthesis_next_best_action" in scores
    assert isinstance(review["strategic_recommendations"], list)


def test_rule_c19_career_firewall_fails_on_leak():
    judge = GraphExtractionJudge(mock=True)
    # Output 03 contaminated with personal career leverage / internal advisory data
    leaky_output_3 = json.dumps({
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:executive",
                    "type": "person",
                    "mutation": "CREATE",
                    "description": "Key target for $1.2M career trajectory positioning and advisory wedge",
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        }
    })
    result = judge.evaluate(leaky_output_3, f1_text="some transcript", mock=True)
    assert result["verdict"] == "FAIL"
    assert result["composite_score"] <= 30
    assert "RULE C-19 VIOLATION" in result["forensic_rationale"]
    assert "$1.2M" in result["forensic_rationale"] or "career leverage" in result["forensic_rationale"]


def test_rule_c19_career_firewall_passes_clean_canonical():
    judge = GraphExtractionJudge(mock=True)
    clean_output_3 = json.dumps({
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:nerc-compliance-engine",
                    "type": "system",
                    "mutation": "CREATE",
                    "description": "NERC CIP-014 compliance engine for transmission substation security",
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        }
    })
    result = judge.evaluate(clean_output_3, f1_text="some transcript", mock=True)
    assert result["verdict"] == "PASS"
    assert result["composite_score"] >= 90
    assert "RULE C-19 VIOLATION" not in result.get("forensic_rationale", "")


def test_restricted_advisory_projection_model():
    node = AgenticLeverageNode(
        node_type="partner_advisory_MARGIN_WEDGE",
        key_players=["Tanner", "Drew Oakley"],
        political_rationale="Client is struggling with internal capacity; PartnerAdvisory can position a dedicated pod.",
        networking_wedge="Present team capacity model directly to Tanner.",
        verbatim_quote="We don't have enough architects for this phase.",
        speaker="Tanner",
    )
    proj = RestrictedAdvisoryProjection(
        advisory_nodes=[node],
        metadata={"project": "nea", "date": "08/27/2026"},
    )
    assert proj.schema_version == "restricted-advisory-1.0"
    assert proj.namespace == INTERNAL_ADVISORY_NAMESPACE
    assert proj.is_quarantined == 1
    assert proj.stealth_score == 1.0
    assert len(proj.advisory_nodes) == 1
    assert proj.advisory_nodes[0].node_type == "partner_advisory_MARGIN_WEDGE"


def test_forensic_transcript_judge_fails_on_career_leak():
    judge = ForensicTranscriptJudge()
    leaky_transcript = "# Meeting Transcript\n[00:01:00] Alex Sanchez: We need to build leverage for the $1.2M career path."
    raw_transcript = "[00:01:00] Alex Sanchez: We need to build leverage."
    res = judge.evaluate(f1_text=leaky_transcript, raw_source_text=raw_transcript, mock=True)
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] <= 30
    assert "RULE C-19 VIOLATION" in res["forensic_rationale"]


def test_executive_recap_judge_fails_on_career_leak():
    judge = ExecutiveRecapJudge()
    leaky_recap = "# Executive Recap\n## Next Steps\n- Use INTERNAL_ADVISORY notes to drive positioning."
    res = judge.evaluate(f2_text=leaky_recap, f1_text="some transcript", mock=True)
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] <= 30
    assert "RULE C-19 VIOLATION" in res["forensic_rationale"]


def test_cross_artifact_reconciler_fails_on_career_leak():
    reconciler = CrossArtifactReconciler()
    metadata = {"date": "08/27/2026"}
    clean_f3 = {"schema_version": "5.7", "graph_mutations": {}}

    # Leak in Output 01
    res1 = reconciler.reconcile(
        f1_text="Prohibited executive compensation band marker",
        f2_text="Clean recap",
        f3_data=clean_f3,
        metadata=metadata,
    )
    assert res1["passed"] is False
    assert any("RULE C-19 VIOLATION" in f for f in res1["failures"])

    # Leak in Output 02
    res2 = reconciler.reconcile(
        f1_text="Clean transcript",
        f2_text="Prohibited networking_wedge details",
        f3_data=clean_f3,
        metadata=metadata,
    )
    assert res2["passed"] is False
    assert any("RULE C-19 VIOLATION" in f for f in res2["failures"])

    # Leak in Output 03
    leaky_f3 = {"schema_version": "5.7", "graph_mutations": {}, "leak": "political_rationale details"}
    res3 = reconciler.reconcile(
        f1_text="Clean transcript",
        f2_text="Clean recap",
        f3_data=leaky_f3,
        metadata=metadata,
    )
    assert res3["passed"] is False
    assert any("RULE C-19 VIOLATION" in f for f in res3["failures"])


def test_qa_gate_cross_4_career_firewall_integrity():
    from dual_pipeline.transcript_qa import run_cross_artifact_consistency

    q_ok = {"passed": True}
    clean_f1 = "Alex Sanchez: The architecture is approved."
    clean_f2 = "# Executive Recap - 08/27/2026\nAlex Sanchez approved the architecture.\n## Next Steps & Action Items\n| Action | Owner |\n| Implement | Alex Sanchez |"
    clean_f3 = json.dumps({"schema_version": "5.7", "graph_mutations": {}})
    meta = {"date": "08/27/2026", "roster": [{"name": "Alex Sanchez"}]}

    # Clean passes Gate Cross 4
    pass_res = run_cross_artifact_consistency(q_ok, q_ok, q_ok, f1_text=clean_f1, f2_text=clean_f2, f3_text=clean_f3, metadata=meta)
    assert pass_res["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]["passed"] is True

    # Leaked Output 1 fails Gate Cross 4
    leak_f1 = "Alex Sanchez: Targeting $1.2m comp package."
    fail_res = run_cross_artifact_consistency(q_ok, q_ok, q_ok, f1_text=leak_f1, f2_text=clean_f2, f3_text=clean_f3, metadata=meta)
    assert fail_res["passed"] is False
    assert fail_res["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]["passed"] is False


def test_agentic_leverage_judge_rejection_low_materiality_or_non_actionable_wedge():
    judge = AgenticLeverageJudge(mock=True)
    # Target present, but wedge is too short / trivial (< 20 chars) -> actionable wedge fails
    node_weak_wedge = AgenticLeverageNode(
        node_type="CAREER_VISIBILITY",
        key_players=["Tanner (VP Cloud)"],
        political_rationale="Latent friction exists but wedge is not formulated well.",
        networking_wedge="Talk to him",  # < 20 chars
        verbatim_quote="We have friction here.",
        speaker="Tanner",
    )
    res = judge.evaluate_agentic_leverage(node_weak_wedge, transcript_text="test transcript", mock=True)
    assert res.pass_gate is False
    assert res.is_actionable_wedge is False

    # Short rationale (< 30 chars) -> materiality < 8
    node_weak_mat = AgenticLeverageNode(
        node_type="CAREER_VISIBILITY",
        key_players=["Tanner (VP Cloud)"],
        political_rationale="Small issue.",  # < 30 chars
        networking_wedge="Schedule 15-min sync with Tanner to brief him on our pre-tested grid compliance blueprint.",
        verbatim_quote="Small issue here.",
        speaker="Tanner",
    )
    res_mat = judge.evaluate_agentic_leverage(node_weak_mat, transcript_text="test transcript", mock=True)
    assert res_mat.pass_gate is False
    assert res_mat.political_materiality < 8


def test_enterprise_graph_review_schema_validation():
    import jsonschema
    schema_path = Path("contracts/evaluation/enterprise-graph-review.schema.json")
    assert schema_path.exists(), "Schema file must exist in contracts/evaluation/"
    schema = json.loads(schema_path.read_text())

    judge = EnterpriseGraphReviewJudge(mock=True)
    canonical_graph = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:grid-platform", "type": "system", "mutation": "CONFIRM"},
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
    }
    review_output = judge.evaluate(canonical_graph, context_text="Context text", mock=True)

    # Must validate cleanly against enterprise-graph-review.schema.json
    jsonschema.validate(instance=review_output, schema=schema)


import unittest


class CareerFirewallAdversarialTests(unittest.TestCase):
    def test_01_gatekeeper_pass(self):
        test_agentic_leverage_judge_gatekeeper_pass()

    def test_02_rejection_target_test(self):
        test_agentic_leverage_judge_rejection_target_test()

    def test_03_evaluate_collection(self):
        test_agentic_leverage_judge_evaluate_collection()

    def test_04_enterprise_graph_review_10_dimensions(self):
        test_enterprise_graph_review_judge_10_dimensions()

    def test_05_rule_c19_firewall_fails_on_leak(self):
        test_rule_c19_career_firewall_fails_on_leak()

    def test_06_rule_c19_firewall_passes_clean_canonical(self):
        test_rule_c19_career_firewall_passes_clean_canonical()

    def test_07_restricted_advisory_projection_model(self):
        test_restricted_advisory_projection_model()

    def test_08_forensic_transcript_judge_fails_on_career_leak(self):
        test_forensic_transcript_judge_fails_on_career_leak()

    def test_09_executive_recap_judge_fails_on_career_leak(self):
        test_executive_recap_judge_fails_on_career_leak()

    def test_10_cross_artifact_reconciler_fails_on_career_leak(self):
        test_cross_artifact_reconciler_fails_on_career_leak()

    def test_11_qa_gate_cross_4_career_firewall_integrity(self):
        test_qa_gate_cross_4_career_firewall_integrity()

    def test_12_agentic_leverage_judge_rejection_low_materiality(self):
        test_agentic_leverage_judge_rejection_low_materiality_or_non_actionable_wedge()

    def test_13_enterprise_graph_review_schema_validation(self):
        test_enterprise_graph_review_schema_validation()
