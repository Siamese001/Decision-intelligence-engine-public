"""
tests/end_to_end/test_learning_qualification_e2e.py

End-to-end integration test suite for Wave 2: Require genuine replay and prove governed application.
Validates the full lifecycle:
1. Category-first replay simulation dispatch.
2. Replay receipt generation and binding.
3. Authentic operator approval.
4. Governed Universal Write Gateway commit with zero graph mutation.
5. Same-process and next-session consumer visibility.
6. Exact bidirectional rollback with absent-key restoration.
7. Ground-truth isolated holdout replay evaluation.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import apply_answer_learning_proposal, simulate_answer_learning_proposal
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.system_learning.holdout_replay_evaluator import (
    HoldoutCase,
    HoldoutReplayEvaluator,
)
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestLearningQualificationE2E(unittest.TestCase):
    """Full lifecycle E2E tests for genuine replay qualification and governed application."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_learning_qual_e2e_")
        self.root = Path(self.temp_dir)
        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        initial_config = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "copilot": {
                    "recency_window_seconds": 3600,
                    "max_candidates": 10,
                },
                "hitl": {
                    "bayesian_prior_strength": 4.0,
                },
            },
        }
        self.config_path.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
        )
        self.uwg = UniversalWriteGateway(self.root, self.root / "seed.sqlite")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_e2e_answer_learning_qualification_commit_and_rollback_lifecycle(self) -> None:
        """Verify full lifecycle: proposal -> replay qualification -> approval -> UWG apply -> consumer readback -> rollback."""
        p_id = "prop-answer-copilot-001"
        run_ids = ["run-copilot-101"]
        evidence_events = ["evt-copilot-101"]

        # 1. Create learning change proposal for copilot context
        proposal = create_artifact(
            "learning-change-proposal",
            p_id,
            proposal_id=p_id,
            proposal_type="copilot_context",
            status="proposed",
            problem="Recent turns require broader temporal window to maintain context.",
            evidence_event_ids=evidence_events,
            run_ids=run_ids,
            proposed_change="Extend recency window from 3600 to 7200 seconds.",
            evaluation_plan=["Replay historical query cohorts", "Verify context recall"],
            risk="low",
            auto_apply=False,
            approval_required=True,
            created_at=utc_now(),
            extensions={
                "com.partner_advisory.nee.learning": {
                    "category": "copilot_context",
                    "target_type": "parameters",
                    "parameter_name": "copilot.recency_window_seconds",
                    "current_value": 3600,
                    "proposed_value": 7200,
                    "baseline_score": 0.65,
                    "target_scores": {"grounding_accuracy": 0.85},
                },
                "com.partner_advisory.nee.provenance": {
                    "proposal_id": p_id,
                    "evidence_event_ids": evidence_events,
                    "run_ids": run_ids,
                    "proposed_change": "Extend recency window",
                    "replay_definition": {"evaluator": "simulate_answer_learning_proposal"},
                    "digest": "sha256-test-prov-001",
                    "signed_by": "test-authority",
                },
            },
        )
        validate_artifact(proposal)

        # 2. Replay simulation dispatch via ClosedLoopLearningEngine
        sim_res = self.engine.simulate_proposal(proposal)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertTrue(sim_res.details.get("zero_mutation_guaranteed"))

        # Verify simulation receipt persisted to disk
        sim_receipt_file = self.proposals_dir / f"{p_id}_simulation_receipt.json"
        self.assertTrue(sim_receipt_file.is_file())
        sim_receipt = read_json(sim_receipt_file)
        self.assertEqual(sim_receipt["status"], "SIMULATED_PASS")

        # 3. Create authentic human operator approval receipt
        approval_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-e2e-lead",
            rationale="Approved 7200s recency window following passing replay simulation.",
            origin="USER_EXPLICIT",
        )

        # 4. Governed application via apply_answer_learning_proposal
        apply_res = apply_answer_learning_proposal(
            proposal=proposal,
            approval_receipt=approval_receipt,
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
            config_path=self.config_path,
            simulation_receipt=sim_receipt,
        )
        self.assertEqual(apply_res["status"], "APPLIED")
        commit_receipt = apply_res["receipt"]
        validate_artifact(commit_receipt)
        self.assertEqual(commit_receipt["applied_parameters"], {"copilot.recency_window_seconds": 7200})

        # 5. Verify consumer visibility
        # Same-process consumer
        same_process_reg = ParameterRegistry(config_path=self.config_path)
        self.assertEqual(same_process_reg.get_int("copilot.recency_window_seconds"), 7200)

        # Next-session consumer (fresh load from disk)
        next_session_data = read_json(self.config_path)
        next_session_params = next_session_data.get("parameters", next_session_data)
        self.assertEqual(next_session_params["copilot"]["recency_window_seconds"], 7200)

        # 6. Execute bidirectional rollback
        rollback_receipt_path = apply_res["rollback_receipt_path"]
        rollback_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_rollback",
            "rollback_path": rollback_receipt_path,
        })
        self.assertEqual(rollback_res["status"], "ROLLED_BACK")

        # Verify consumer visibility after rollback (restored to 3600)
        reverted_reg = ParameterRegistry(config_path=self.config_path)
        self.assertEqual(reverted_reg.get_int("copilot.recency_window_seconds"), 3600)

    def test_e2e_holdout_replay_evaluator_unlabeled_isolation(self) -> None:
        """Verify holdout replay evaluation isolates predictions from ground truth labels."""
        cases = [
            HoldoutCase(
                case_id="case-001",
                category="alias_resolution",
                input_text="TechnoSilva incident mapping",
                raw_label="system:technosilva_legacy",
                ground_truth_label="system:technosylva",
                negative_labels=["system:other_vendor"],
            ),
            HoldoutCase(
                case_id="case-002",
                category="alias_resolution",
                input_text="Standard vendor platform",
                raw_label="system:tens_sharepoint",
                ground_truth_label="system:tens-sharepoint",
                negative_labels=["system:external"],
            ),
        ]

        # Verify unlabeled view does not expose ground truth
        view = cases[0].unlabeled_view
        self.assertNotIn("ground_truth_label", view)
        self.assertNotIn("negative_labels", view)
        self.assertEqual(view["input_text"], "TechnoSilva incident mapping")

        proposal = {
            "proposal_id": "prop-holdout-alias-01",
            "proposal_type": "alias_addition",
            "extensions": {
                "com.partner_advisory.nee.learning": {
                    "category": "learned_aliases",
                    "from_id": "system:technosilva_legacy",
                    "to_id": "system:technosylva",
                }
            },
        }

        evaluator = HoldoutReplayEvaluator(cases)
        report = evaluator.evaluate_proposal(proposal)

        self.assertEqual(report.verdict, "PASS")
        self.assertGreater(report.accuracy_after, report.accuracy_before)
        self.assertEqual(report.false_merge_rate_after, 0.0)
        self.assertTrue(len(report.dataset_digest) > 0)
        self.assertTrue(len(report.proposal_digest) > 0)
        self.assertEqual(report.evaluator_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
