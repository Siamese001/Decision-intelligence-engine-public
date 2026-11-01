#!/usr/bin/env python3
"""End-to-End Test Suite for Governed HITL Closed-Loop Learning Lifecycle.

Validates the formal 5-phase closed loop (I-4):
[1. INGEST] -> [2. CLUSTER] -> [3. PROPOSE] -> [4. SIMULATE] -> [5. APPROVE & APPLY] + L4 Seal.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import (
    create_signed_calibration_approval_receipt,
    verify_calibration_approval,
)
from dual_pipeline.hitl_governance import (
    HITLDecisionStore,
    validate_approval_origin,
)
from dual_pipeline.hitl_governance_core.crypto import get_governed_hitl_keypair
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.telemetry_core.recorder import RunRecorder, verify_run_l4_seal


class TestHitlLearningLifecycleE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.runtime_root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.runtime_root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        self.hitl_ledger_file = self.ledger_dir / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(ledger_path=self.hitl_ledger_file)
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )

        self.priv_key, self.pub_key = get_governed_hitl_keypair(key_dir=self.tmp_path / "keys")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_end_to_end_5_phase_hitl_learning_lifecycle(self) -> None:
        category = "entity_resolution"
        options = [
            {"key": "merge_nodes", "label": "Merge Nodes", "confidence_score": 0.60},
            {"key": "isolate_nodes", "label": "Isolate Nodes", "confidence_score": 0.50},
        ]

        # Phase 1: INGEST - Seed historical decisions with repeated manual overrides
        for i in range(4):
            self.store.record_decision(
                decision_id=f"dec-er-{i}",
                category=category,
                prompt="Select resolution strategy",
                options=options,
                selected_option=options[1],  # Override recommended top option
                operator_id="human_expert",
                metadata={"outcome_type": "OVERRIDDEN"},
            )

        decisions = self.engine.ingest_hitl_decisions(hitl_ledger_path=self.hitl_ledger_file)
        self.assertGreaterEqual(len(decisions), 4)
        overrides = [d for d in decisions if d.get("outcome_type") == "OVERRIDDEN"]
        self.assertGreaterEqual(len(overrides), 4)

        # Phase 2: CLUSTER - Group into recurring failure cohort
        cohorts = self.engine.analyze_failure_cohorts(overrides, min_occurrences=2)
        self.assertGreaterEqual(len(cohorts), 1)
        target_cohort = next(c for c in cohorts if category in c["cohort_key"])
        self.assertGreaterEqual(target_cohort["occurrence_count"], 4)

        # Phase 3: PROPOSE - Synthesize schema-valid learning-change-proposal:1.0.0
        proposal = self.engine.generate_proposal_for_cohort(
            target_cohort,
            proposal_type="hitl_confidence",
            target_override={
                "category": category,
                "parameter_name": "hitl.bayesian_prior_strength",
            },
        )

        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        self.assertEqual(len(proposal["evidence_event_ids"]), len(set(proposal["evidence_event_ids"])))
        self.assertEqual(proposal["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        ext = proposal["extensions"]["com.partner_advisory.nee.learning"]
        self.assertEqual(ext["parameter_name"], "hitl.bayesian_prior_strength")
        self.assertLess(ext["proposed_value"], ext["current_value"])
        self.assertGreaterEqual(ext["proposed_value"], 1.0)
        self.assertLessEqual(ext["proposed_value"], 10.0)

        # Phase 4: SIMULATE - Verify dry-run Brier score improvement (Δ_Brier < 0) & bounds
        sim_res = self.engine.simulate_proposal(proposal)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertIn("brier_score_delta", sim_res.details)
        self.assertLess(sim_res.details["brier_score_delta"], 0.0)
        self.assertEqual(sim_res.details["bounds_check"], "VALID")

        # Phase 5: APPROVE & L4 SEAL
        # Generate signed approval receipt with Ed25519 signature
        signed_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="human_governance_lead",
            private_key=self.priv_key,
            public_key=self.pub_key,
            user_command="implement learning proposal",
        )

        # Verify cryptographic approval binding
        is_approved, msg = self.engine.verify_proposal_approval(proposal, signed_receipt)
        self.assertTrue(is_approved, msg)
        self.assertTrue(verify_calibration_approval(proposal, signed_receipt))

        # Verify anti-synthetic origin firewall
        is_human, origin_reason = validate_approval_origin(signed_receipt)
        self.assertTrue(is_human, origin_reason)

        # Record and L4-seal the learning run
        recorder = RunRecorder(
            runtime_root=self.runtime_root,
            run_id=f"run-learn-{proposal['proposal_id']}",
            trace_id="a" * 32,
            use_case="learning",
            graph_snapshot=None,
        )
        corr = {
            "source_id": None,
            "candidate_id": None,
            "query_id": None,
            "graph_operation_id": None,
            "hitl_item_id": f"item-{proposal['proposal_id']}",
        }
        for stage in ("LP0", "LP1", "LP2", "LP3", "LP4"):
            recorder.record(
                stage=stage,
                event_type="pipeline_event",
                payload={"proposal_id": proposal["proposal_id"], "stage": stage},
                correlation=corr,
                status="PASS",
                record_count=1,
            )
        seal_result = recorder.seal(
            required_stages=["LP0", "LP1", "LP2", "LP3", "LP4", "L4"],
            correlation=corr,
        )
        recorder.close()

        self.assertEqual(seal_result["status"], "PASS")

        # Verify L4 seal via independent validator
        verify_seal = verify_run_l4_seal(self.runtime_root, f"run-learn-{proposal['proposal_id']}")
        self.assertEqual(verify_seal["status"], "PASS")
        self.assertTrue(verify_seal["seal_verified"])
        self.assertEqual(verify_seal["event_chain_digest"], seal_result["event_chain_digest"])

    def test_multi_parameter_proposal_generation(self) -> None:
        """Verify proposal generation supports all 4 SSOT parameters with data-derived values."""
        category = "speaker_attribution"
        cohort = {
            "cohort_key": f"hitl_override:{category}",
            "occurrence_count": 6,
            "run_ids": ["run-speaker-1"],
            "items": [{"decision_id": f"d-{i}", "outcome_type": "OVERRIDDEN"} for i in range(6)],
        }

        params = [
            ("hitl.bayesian_prior_strength", 4.0, 1.0, 10.0),
            ("hitl.human_feedback_weight", 1.0, 0.5, 2.0),
            ("hitl.autonomous_feedback_weight", 0.05, 0.01, 0.20),
            ("hitl.ambiguity_margin_threshold", 0.15, 0.05, 0.30),
        ]

        for pname, _cval, min_b, max_b in params:
            proposal = self.engine.generate_proposal_for_cohort(
                cohort,
                proposal_type="hitl_confidence",
                target_override={"parameter_name": pname, "category": category},
            )
            ext = proposal["extensions"]["com.partner_advisory.nee.learning"]
            self.assertEqual(ext["parameter_name"], pname)
            self.assertGreaterEqual(ext["proposed_value"], min_b)
            self.assertLessEqual(ext["proposed_value"], max_b)


if __name__ == "__main__":
    unittest.main()
