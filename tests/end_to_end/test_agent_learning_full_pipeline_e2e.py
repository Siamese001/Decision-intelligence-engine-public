"""
tests/end_to_end/test_agent_learning_full_pipeline_e2e.py

Wave 5 Full Lifecycle Integration Test Suite.
Exercises the entire agent learning and autonomy lifecycle across isolated test sandboxes:
1. Operator correction capture -> holdout replay -> proposal synthesis -> operator approval -> rollback verification.
2. Requirement-scoped query recovery lifecycle with bounded attempt budget.
3. QA defect evaluation with selective specialist routing and bounded retry.
4. Harm-prioritized graph maintenance with query impact joining and zero canonical graph mutation.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.feature_flags import FeatureFlagState, get_feature_flag_manager
from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
)
from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
    OperatorCorrection,
)
from dual_pipeline.learning_engine_core.rollback import (
    execute_rollback,
    record_rollback_snapshot,
)
from dual_pipeline.maintenance_swarm import MaintenanceSubAgent
from dual_pipeline.retrieval.agents.bounded_query_recovery_agent import (
    BoundedQueryRecoveryAgent,
)
from dual_pipeline.system_learning.holdout_replay_evaluator import (
    HoldoutCase,
    HoldoutReplayEvaluator,
)
from dual_pipeline.transcript_qa_core.selective_defect_evaluator import (
    SelectiveDefectEvaluator,
)


class TestAgentLearningFullPipelineE2E:

    def test_full_lifecycle_operator_correction_to_rollback(self) -> None:
        """Exercises complete lifecycle: Capture -> Holdout Eval -> Proposal -> Rollback."""
        with tempfile.TemporaryDirectory(prefix="full_pipeline_test_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            proposals_dir = tmp_path / "proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            aliases_file = tmp_path / "learned_aliases.json"
            aliases_file.write_text(json.dumps({"aliases": {}}), encoding="utf-8")

            # 1. Capture operator corrections
            ingestor = CorrectionFeedbackIngestor(min_confirmations=2)
            ingestor.record_speaker_correction(
                raw_speaker="spk_unknown_1",
                corrected_speaker="Alice Smith",
                operator_id="operator-alice",
                session_id="session-e2e",
            )
            ingestor.record_speaker_correction(
                raw_speaker="spk_unknown_1",
                corrected_speaker="Alice Smith",
                operator_id="operator-bob",
                session_id="session-e2e",
            )

            # 2. Holdout cases
            evaluator = HoldoutReplayEvaluator()
            holdout_cases = [
                HoldoutCase(
                    case_id="case-1",
                    category="speaker_attribution",
                    input_text="spk_unknown_1: Here is the Q3 update.",
                    raw_label="spk_unknown_1",
                    ground_truth_label="Alice Smith",
                ),
                HoldoutCase(
                    case_id="case-2",
                    category="speaker_attribution",
                    input_text="spk_unknown_1: Confirming next steps.",
                    raw_label="spk_unknown_1",
                    ground_truth_label="Alice Smith",
                ),
            ]

            # 3. Synthesize proposals
            proposals, reports = ingestor.synthesize_proposals(
                evaluator=evaluator,
                holdout_cases=holdout_cases,
            )
            assert len(proposals) == 1
            proposal = proposals[0]
            validate_artifact(proposal)
            assert proposal["approval_required"] is True
            assert proposal["auto_apply"] is False

            # 4. Record pre-mutation rollback snapshot
            pre_state = {
                "from_id": "spk_unknown_1",
                "previous_target": None,
                "target_file": str(aliases_file),
            }
            snap = record_rollback_snapshot(proposals_dir, proposal, pre_state)
            assert snap["status"] == "RECORDED"

            # 5. Simulate approved mutation
            aliases_data = json.loads(aliases_file.read_text(encoding="utf-8"))
            aliases_data["aliases"]["spk_unknown_1"] = "Alice Smith"
            aliases_file.write_text(json.dumps(aliases_data), encoding="utf-8")

            # 6. Verify mutation is active
            mutated = json.loads(aliases_file.read_text(encoding="utf-8"))
            assert mutated["aliases"]["spk_unknown_1"] == "Alice Smith"

            # 7. Execute Rollback
            rb_report = execute_rollback(
                proposals_dir=proposals_dir,
                proposal_id=proposal["proposal_id"],
                aliases_path=aliases_file,
            )
            assert rb_report["status"] == "ROLLED_BACK"

            # 8. Verify restored state
            restored = json.loads(aliases_file.read_text(encoding="utf-8"))
            assert "spk_unknown_1" not in restored["aliases"]

    def test_full_lifecycle_requirement_scoped_query_recovery(self) -> None:
        """Exercises bounded query recovery when facets are missing and budget permits."""
        agent = BoundedQueryRecoveryAgent(max_iterations=1, mock_mode=True)
        flag_mgr = get_feature_flag_manager()

        query = "What is the technical architecture and delivery timeline for the vector engine?"
        required_facets = ["technical_architecture", "delivery_timeline"]

        candidate_facts = [
            {
                "node_id": "fact-tech-01",
                "name": "BGE-M3 Architecture",
                "content": "BGE-M3 vector search.",
                "facet": "technical_architecture",
                "category": "technical",
            },
            {
                "node_id": "fact-comm-01",
                "name": "Delivery Timeline",
                "content": "Phase 2 delivery deadline is Q4 2026.",
                "facet": "delivery_timeline",
                "category": "commercial",
            },
        ]
        initial_fused = [candidate_facts[0]]

        with flag_mgr.override("BOUNDED_QUERY_RECOVERY", FeatureFlagState.ON):
            # Attempt 1: Successful recovery
            updated_fused, receipt = agent.recover(
                query=query,
                fused_facts=initial_fused,
                candidate_facts=candidate_facts,
                required_facets=required_facets,
                iteration=1,
                remaining_budget=1,
            )
            assert receipt.is_success is True
            assert receipt.terminal_reason == "RECOVERY_SUCCESS"
            assert len(updated_fused) == 2
            assert "delivery_timeline" in receipt.recovered_facets

            # Attempt 2: Exceeding budget or iteration cap fails closed
            _, exhausted = agent.recover(
                query=query,
                fused_facts=initial_fused,
                candidate_facts=candidate_facts,
                required_facets=required_facets,
                iteration=2,
                remaining_budget=0,
            )
            assert exhausted.is_success is False
            assert exhausted.terminal_reason in ("BUDGET_EXHAUSTED", "MAX_ITERATIONS_REACHED")

    def test_full_lifecycle_qa_defect_evaluation_and_selective_routing(self) -> None:
        """Exercises selective defect evaluation, selective routing, and bounded healing."""
        evaluator = SelectiveDefectEvaluator(max_retries=2)

        f1 = "Alice Smith: Good morning team.\nBob Jones: Let's discuss."
        f2 = "Attendees: Alice Smith, Bob Jones\nSummary: Sync."
        broken_f3 = "{bad_json"

        report = evaluator.evaluate_all(f1, f2, broken_f3)
        assert report.passed is False
        assert "schema" in report.routed_specialists
        assert "speaker" not in report.routed_specialists

        # Bounded healing repair
        def healer(in_f1: str, in_f2: str, in_f3: Any, routed: Dict[str, Any]) -> Tuple[str, str, Any]:
            return in_f1, in_f2, json.dumps({"canonical": True})

        final_rep, attempts = evaluator.evaluate_and_route_with_retry(
            f1, f2, broken_f3, healer_fn=healer
        )
        assert final_rep.passed is True
        assert final_rep.terminal_reason == "ALL_DIMENSIONS_PASSED"
        assert attempts == 1

    def test_full_lifecycle_harm_prioritized_maintenance_and_zero_mutation(self) -> None:
        """Exercises harm prioritization, alert deduplication, and zero canonical graph writes."""
        with tempfile.TemporaryDirectory(prefix="graph_zero_mut_") as tmp_dir:
            db_path = Path(tmp_dir) / "prod_graph.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT);")
            conn.execute("INSERT INTO entities VALUES ('org:partner_advisory', 'PartnerAdvisory Consulting');")
            conn.commit()
            conn.close()

            # Multiple findings for the same cluster
            findings = [
                HygieneFinding(
                    finding_id="find-001",
                    category="duplicate_node",
                    severity="HIGH",
                    target_entities=["org:partner_advisory", "org:partner_advisory_consulting"],
                    description="Duplicate 1",
                    proposed_fix="merge org:partner_advisory_consulting into org:partner_advisory",
                ),
                HygieneFinding(
                    finding_id="find-002",
                    category="duplicate_node",
                    severity="HIGH",
                    target_entities=["org:partner_advisory", "org:partner_advisory_consulting"],
                    description="Duplicate 2 (recurrence)",
                    proposed_fix="merge org:partner_advisory_consulting into org:partner_advisory",
                    recurrence_count=2,
                ),
            ]

            impacts = [
                QueryImpactRecord(
                    entity_id="org:partner_advisory",
                    query_frequency=50,
                    failure_rate=0.30,
                )
            ]

            planner = HarmPrioritizedPlanner(run_id="run-w5-e2e")
            plan = planner.plan_maintenance(findings, impacts)

            assert plan.total_findings == 2
            assert plan.deduplicated_count == 1
            assert len(plan.proposals) == 1

            prop = plan.proposals[0]
            assert prop.harm_score > 50.0
            validate_artifact(prop.artifact)
            assert prop.artifact["approval_required"] is True

            # Verify zero DB mutation
            check_conn = sqlite3.connect(db_path)
            rows = check_conn.execute("SELECT * FROM entities;").fetchall()
            check_conn.close()
            assert len(rows) == 1
            assert rows[0] == ("org:partner_advisory", "PartnerAdvisory Consulting")
