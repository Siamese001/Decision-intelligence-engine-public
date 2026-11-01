"""
tests/test_agent_learning_wave0_baseline.py

Unit tests for Wave 0: Agent Learning & Autonomy Opportunities Baseline.
Validates the empirical agent/role registry snapshot, AgentOutcomeAdapter,
agent-outcome-event:1.0.0 schema conformance, and fail-closed proposal simulation.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import ROOT, read_json
from dual_pipeline.learning_adapters import AgentOutcomeAdapter, AgentOutcomeEvent, OutcomeType
from dual_pipeline.learning_engine import ClosedLoopLearningEngine, SimulationResult, _derive_cohort_key
from tools.snapshot_agent_learning_registry import build_registry_snapshot


class AgentLearningWave0BaselineTests(unittest.TestCase):
    def test_01_registry_snapshot_structure_and_coverage_baseline(self) -> None:
        """Verify the comprehensive agent and role registry snapshot."""
        snapshot = build_registry_snapshot()
        self.assertIn("metadata", snapshot)
        self.assertIn("statistics", snapshot)
        self.assertIn("entry_points", snapshot)
        self.assertIn("components", snapshot)

        stats = snapshot["statistics"]
        self.assertGreaterEqual(stats["total_registered_components"], 50)
        self.assertGreaterEqual(stats["total_declared_roles"], 90)
        # Empirical baseline: exactly 1 agent currently has durable learning (HitlArbiterAgent)
        self.assertEqual(stats["components_with_durable_learning"], 1)
        self.assertGreaterEqual(stats["components_with_within_run_adaptation"], 15)
        self.assertGreaterEqual(stats["components_with_static_or_simulated"], 35)
        self.assertLess(stats["durable_learning_coverage_pct"], 5.0)

        # Verify all 6 real entry points are mapped
        entry_points = snapshot["entry_points"]
        expected_eps = {
            "adjudication_error",
            "retrieval_miss",
            "speaker_correction",
            "decision_override",
            "quality_check_failure",
            "maintenance_finding",
        }
        self.assertEqual(set(entry_points.keys()), expected_eps)

        # Check HitlArbiterAgent is the one durable learning component
        durable_agents = [c for c in snapshot["components"] if c["has_durable_learning"]]
        self.assertEqual(len(durable_agents), 1)
        self.assertEqual(durable_agents[0]["name"], "HitlArbiterAgent")

    def test_02_adjudication_error_outcome_adaptation_and_contract(self) -> None:
        """Verify adjudication error mapping to agent-outcome-event and learning-event."""
        event = AgentOutcomeAdapter.build_adjudication_error_event(
            conflicting_entities=["entity:technosilva_legacy", "entity:technosylva"],
            reason="Ambiguous entity alias during tech architecture extraction",
            candidate_id="cand-tech-001",
            corrected_canonical="entity:technosylva",
            receipt_id="receipt-adj-101",
        )
        self.assertEqual(event.outcome_type, OutcomeType.ADJUDICATION_ERROR)
        self.assertEqual(event.stage, "L2")

        # Validate agent-outcome-event contract artifact
        art = AgentOutcomeAdapter.to_contract_artifact(event)
        self.assertEqual(art["contract_id"], "urn:nee:dual-pipeline:agent-outcome-event:1.0.0")
        validate_artifact(art)

        # Validate conversion to learning-event:1.0.0
        learn_evt = AgentOutcomeAdapter.to_learning_event(event)
        self.assertEqual(learn_evt["contract_id"], "urn:nee:dual-pipeline:learning-event:1.0.0")
        self.assertEqual(learn_evt["event_type"], "entity_resolution_decision")
        self.assertEqual(learn_evt["correlation"]["candidate_id"], "cand-tech-001")
        validate_artifact(learn_evt)

    def test_03_retrieval_miss_outcome_adaptation_and_contract(self) -> None:
        """Verify retrieval coverage gap mapping to agent-outcome-event and learning-event."""
        event = AgentOutcomeAdapter.build_retrieval_miss_event(
            query_text="What are the fire safety allocations for Technosylva?",
            missing_requirements=["req:budget_table_2026", "req:fire_spread_model"],
            retrieved_count=2,
        )
        self.assertEqual(event.outcome_type, OutcomeType.RETRIEVAL_MISS)
        self.assertEqual(event.stage, "L1")

        art = AgentOutcomeAdapter.to_contract_artifact(event)
        validate_artifact(art)

        learn_evt = AgentOutcomeAdapter.to_learning_event(event)
        self.assertEqual(learn_evt["event_type"], "retrieval_event")
        validate_artifact(learn_evt)

    def test_04_speaker_correction_and_decision_override_adaptation(self) -> None:
        """Verify speaker correction and HITL decision override adaptation."""
        # Speaker correction
        spk_evt = AgentOutcomeAdapter.build_speaker_correction_event(
            assigned_speaker="Speaker 2",
            confirmed_speaker="Amit Ayer",
            turn_ids=["t1", "t2"],
        )
        self.assertEqual(spk_evt.outcome_type, OutcomeType.SPEAKER_CORRECTION)
        spk_art = AgentOutcomeAdapter.to_contract_artifact(spk_evt)
        validate_artifact(spk_art)
        spk_learn = AgentOutcomeAdapter.to_learning_event(spk_evt)
        validate_artifact(spk_learn)

        # Decision override
        dec_evt = AgentOutcomeAdapter.build_decision_override_event(
            decision_id="dec_prompt_001",
            category="prompt_governance",
            recommended_option="Option A: Keep Strict 50-word cap",
            operator_chosen_option="Option B: Allow 90-word concise budget",
            margin_at_decision=0.12,
        )
        self.assertEqual(dec_evt.outcome_type, OutcomeType.DECISION_OVERRIDE)
        dec_art = AgentOutcomeAdapter.to_contract_artifact(dec_evt)
        validate_artifact(dec_art)
        dec_learn = AgentOutcomeAdapter.to_learning_event(dec_evt)
        validate_artifact(dec_learn)

    def test_05_quality_failure_and_maintenance_finding_adaptation(self) -> None:
        """Verify QA quality defect and maintenance finding adaptation."""
        # Quality failure
        qa_evt = AgentOutcomeAdapter.build_quality_failure_event(
            gate_name="Gate 1.7",
            defect_dimension="speaker_attribution",
            failure_message="Single-speaker collapse detected: 92% turn share",
            artifact_id="output_01_transcript",
        )
        self.assertEqual(qa_evt.outcome_type, OutcomeType.QUALITY_CHECK_FAILURE)
        qa_art = AgentOutcomeAdapter.to_contract_artifact(qa_evt)
        validate_artifact(qa_art)
        qa_learn = AgentOutcomeAdapter.to_learning_event(qa_evt)
        validate_artifact(qa_learn)

        # Maintenance finding
        maint_evt = AgentOutcomeAdapter.build_maintenance_finding_event(
            finding_type="duplicate_vendor_node",
            entity_ids=["vendor:partner_advisory_build", "vendor:partner_advisory_consulting"],
            description="Merged vendor entity nodes detected in canonical graph",
        )
        self.assertEqual(maint_evt.outcome_type, OutcomeType.MAINTENANCE_FINDING)
        maint_art = AgentOutcomeAdapter.to_contract_artifact(maint_evt)
        validate_artifact(maint_art)
        maint_learn = AgentOutcomeAdapter.to_learning_event(maint_evt)
        validate_artifact(maint_learn)

    def test_06_cohort_key_derivation_and_cohort_grouping(self) -> None:
        """Verify that agent outcome events produce distinct, structured cohort keys."""
        item1 = AgentOutcomeAdapter.to_cohort_item(
            AgentOutcomeAdapter.build_adjudication_error_event(["e1", "e2"], "mismatch")
        )
        item2 = AgentOutcomeAdapter.to_cohort_item(
            AgentOutcomeAdapter.build_retrieval_miss_event("query", ["r1"])
        )
        item3 = AgentOutcomeAdapter.to_cohort_item(
            AgentOutcomeAdapter.build_speaker_correction_event("Room-1", "Dan")
        )

        self.assertEqual(_derive_cohort_key(item1), "outcome:adjudication_error:entity_resolution")
        self.assertEqual(_derive_cohort_key(item2), "outcome:retrieval_miss:coverage_gap")
        self.assertEqual(_derive_cohort_key(item3), "outcome:speaker_correction:speaker_disambiguation")

    def test_07_simulate_proposal_fail_closed_on_unsupported_types(self) -> None:
        """Verify that unsupported proposal types fail closed with SIMULATED_FAIL."""
        engine = ClosedLoopLearningEngine()
        unsupported_proposal = {
            "proposal_id": "prop-magic-001",
            "proposal_type": "unregistered_magic_adaptation",
            "status": "proposed",
            "problem": "Test unsupported proposal",
            "proposed_change": "Execute magic neural update",
            "evaluation_plan": ["None"],
            "risk": "high",
            "approval_required": True,
            "auto_apply": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extensions": {
                "com.partner_advisory.nee.learning": {
                    "cohort_key": "test:unsupported",
                    "target_type": "unregistered_magic_adaptation",
                }
            },
        }
        res = engine.simulate_proposal(unsupported_proposal)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "SIMULATED_FAIL")
        self.assertIn("no replay simulator", res.details.get("error", "").lower())
