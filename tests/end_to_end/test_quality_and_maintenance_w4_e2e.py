"""
tests/end_to_end/test_quality_and_maintenance_w4_e2e.py

End-to-end test suite for Wave 4: Quality & Maintenance.
Verifies:
1. 100% seeded defect detection across schema, speaker, provenance, and cross-artifact dimensions.
2. Selective routing to specialists omitting passing dimensions.
3. Bounded retry healing lifecycle.
4. Harm-prioritized maintenance planning, query impact joining, and contract-valid proposals.
5. QASwarm integration with selective defect evaluation.
"""

import json
from typing import Any, Dict, Tuple

import pytest

from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
)
from dual_pipeline.transcript_qa_core.qa_swarm import QASwarm
from dual_pipeline.transcript_qa_core.selective_defect_evaluator import (
    SelectiveDefectEvaluator,
)


class TestQualityAndMaintenanceW4E2E:

    def test_e2e_seeded_defect_detection_100_percent(self) -> None:
        """Evaluator must detect 100% of seeded defects with zero false PASS results."""
        evaluator = SelectiveDefectEvaluator()

        clean_f1 = "Alice Smith: Good morning team.\nBob Jones: Thanks Alice, reviewing roadmap."
        clean_f2 = "Attendees: Alice Smith, Bob Jones\nKey Decisions: Approved roadmap."
        clean_f3 = json.dumps({
            "canonical": True,
            "assertions": [{"id": "a1", "evidence_refs": ["turn_1"]}],
        })

        # Baseline clean run
        clean_report = evaluator.evaluate_all(clean_f1, clean_f2, clean_f3)
        assert clean_report.passed is True
        assert clean_report.status == "PASS"

        # 1. Seed Schema Defect: Invalid JSON in Output 3
        bad_f3_schema = "{not valid json}"
        rep_schema = evaluator.evaluate_all(clean_f1, clean_f2, bad_f3_schema)
        assert rep_schema.passed is False
        assert rep_schema.dimension_results["schema"].status == "FAIL"
        assert len(rep_schema.dimension_results["schema"].findings) > 0

        # 2. Seed Speaker Defect: Unresolved Speaker placeholder in Output 1
        bad_f1_speaker = "Speaker Unknown: We need to push the deployment.\nBob Jones: Agreed."
        rep_speaker = evaluator.evaluate_all(bad_f1_speaker, clean_f2, clean_f3)
        assert rep_speaker.passed is False
        assert rep_speaker.dimension_results["speaker"].status == "FAIL"

        # 3. Seed Provenance Defect: Assertion without evidence references in Output 3
        bad_f3_prov = json.dumps({
            "canonical": True,
            "assertions": [{"id": "a2", "statement": "Revenue increased by 40%"}],
        })
        rep_prov = evaluator.evaluate_all(clean_f1, clean_f2, bad_f3_prov)
        assert rep_prov.passed is False
        assert rep_prov.dimension_results["provenance"].status == "FAIL"

        # 4. Seed Cross-Artifact Defect: Attendee in recap not present in transcript
        bad_f2_cross = "Attendees: Alice Smith, Bob Jones, Ghost Attendee\nDecisions: Done."
        rep_cross = evaluator.evaluate_all(clean_f1, bad_f2_cross, clean_f3)
        assert rep_cross.passed is False
        assert rep_cross.dimension_results["cross_artifact"].status == "FAIL"

    def test_e2e_selective_routing_to_specialists(self) -> None:
        """Only defective dimensions must be routed to specialists; passing ones are omitted."""
        evaluator = SelectiveDefectEvaluator()

        f1 = "Alice Smith: Good morning team.\nBob Jones: Reviewing roadmap."
        f2 = "Attendees: Alice Smith, Bob Jones\nKey Decisions: Roadmap approved."
        bad_f3 = "{invalid json}"

        report = evaluator.evaluate_all(f1, f2, bad_f3)
        assert report.passed is False

        # Verify only schema is in routed_specialists
        assert "schema" in report.routed_specialists
        assert report.routed_specialists["schema"]["specialist"] == "Output3SchemaValidator"
        assert report.routed_specialists["schema"]["healer"] == "table_healer"

        # Verify passing dimensions are NOT routed
        assert "speaker" not in report.routed_specialists
        assert "provenance" not in report.routed_specialists
        assert "cross_artifact" not in report.routed_specialists

    def test_e2e_bounded_retry_healing_lifecycle(self) -> None:
        """Bounded retry loop repairs defects within budget and terminates with pass."""
        evaluator = SelectiveDefectEvaluator(max_retries=2)

        f1 = "Alice Smith: Reviewing roadmap.\nBob Jones: Agreed."
        f2 = "Attendees: Alice Smith, Bob Jones\nDecisions: Approved."
        broken_f3 = "{malformed json"

        def mock_healer(
            in_f1: str, in_f2: str, in_f3: Any, routed: Dict[str, Any]
        ) -> Tuple[str, str, Any]:
            if "schema" in routed:
                healed_f3 = json.dumps({"canonical": True, "assertions": [{"id": "a1", "evidence_refs": ["t1"]}]})
                return in_f1, in_f2, healed_f3
            return in_f1, in_f2, in_f3

        final_report, attempts = evaluator.evaluate_and_route_with_retry(
            f1, f2, broken_f3, healer_fn=mock_healer
        )

        assert final_report.passed is True
        assert final_report.terminal_reason == "ALL_DIMENSIONS_PASSED"
        assert attempts == 1  # Fixed on 1st retry

    def test_e2e_harm_prioritized_maintenance_planner_ranking(self) -> None:
        """HarmPrioritizedPlanner must rank proposals by observed harm and generate contract-valid proposals."""
        planner = HarmPrioritizedPlanner(run_id="test-run-w4")

        findings = [
            HygieneFinding(
                finding_id="find-01",
                category="duplicate_node",
                severity="HIGH",
                target_entities=["org:partner_advisory", "org:partner_advisory_consulting"],
                description="Duplicate entity pair in knowledge graph.",
                proposed_fix="merge org:partner_advisory_consulting into org:partner_advisory",
                recurrence_count=3,
            ),
            HygieneFinding(
                finding_id="find-02",
                category="stale_assertion",
                severity="MEDIUM",
                target_entities=["assertion:cloud_spend_2025"],
                description="Assertion expired TTL.",
                proposed_fix="archive_assertion",
                recurrence_count=1,
            ),
            HygieneFinding(
                finding_id="find-03",
                category="orphan_clutter",
                severity="LOW",
                target_entities=["node:unreferenced_scratch"],
                description="Orphan node with no incident edges.",
                proposed_fix="prune_node",
                recurrence_count=1,
            ),
        ]

        query_impacts = [
            QueryImpactRecord(
                entity_id="org:partner_advisory",
                query_frequency=30,
                failure_rate=0.40,
                mrr_drop=0.20,
            ),
            QueryImpactRecord(
                entity_id="assertion:cloud_spend_2025",
                query_frequency=5,
                failure_rate=0.0,
                mrr_drop=0.0,
            ),
        ]

        plan = planner.plan_maintenance(findings, query_impacts)

        assert len(plan.proposals) == 3
        # Strict harm ordering: PartnerAdvisory (HIGH + 30 q + recurrence) > Stale (MED + 5 q) > Orphan (LOW + 0 q)
        assert plan.proposals[0].target_entities == ["org:partner_advisory", "org:partner_advisory_consulting"]
        assert plan.proposals[1].target_entities == ["assertion:cloud_spend_2025"]
        assert plan.proposals[2].target_entities == ["node:unreferenced_scratch"]

        assert plan.proposals[0].harm_score > plan.proposals[1].harm_score > plan.proposals[2].harm_score

        # Verify contract validity
        top_artifact = plan.proposals[0].artifact
        assert top_artifact["contract_id"] == "urn:nee:dual-pipeline:learning-change-proposal:1.0.0"
        assert top_artifact["schema_version"] == "1.0.0"
        assert top_artifact["proposal_type"] == "workflow"
        assert top_artifact["status"] == "proposed"
        assert top_artifact["approval_required"] is True
        assert top_artifact["auto_apply"] is False
        assert "canonical_digest" in top_artifact
        assert "extensions" in top_artifact

    def test_e2e_qa_swarm_selective_evaluation_bridge(self) -> None:
        """QASwarm delegating to SelectiveDefectEvaluator reports authentic findings."""
        evaluator = SelectiveDefectEvaluator()
        swarm = QASwarm(evaluator=evaluator)

        clean_f1 = "Alice Smith: Good morning.\nBob Jones: Hello Alice."
        clean_f2 = "Attendees: Alice Smith, Bob Jones\nSummary: Sync meeting."
        clean_f3 = json.dumps({"canonical": True, "nodes": []})

        clean_res = swarm.evaluate_all(clean_f1, clean_f2, clean_f3)
        assert clean_res["swarm_passed"] is True

        # Defect in f1
        bad_f1 = "Speaker Unknown: Roadmap review."
        defect_res = swarm.evaluate_all(bad_f1, clean_f2, clean_f3)
        assert defect_res["swarm_passed"] is False
        assert defect_res["agent_results"]["Speaker Attribution Validator"]["status"] == "FAIL"
