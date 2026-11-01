"""tests/test_uwg_learning_commit_e2e.py — End-to-end tests for UWG learning_parameter_commit lane."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import (
    create_calibration_proposal,
    create_signed_calibration_approval_receipt,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, utc_now
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestUwgLearningCommitE2E(unittest.TestCase):
    """End-to-end tests for Universal Write Gateway learning parameter commits and rollback."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_uwg_commit_e2e_")
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
                "hitl": {
                    "bayesian_prior_strength": 4.0,
                    "human_feedback_weight": 1.0,
                    "autonomous_feedback_weight": 0.05,
                    "ambiguity_margin_threshold": 0.15,
                }
            },
        }
        self.config_path.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

        self.uwg = UniversalWriteGateway(self.root, self.root / "seed.sqlite")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_uwg_learning_parameter_commit_lifecycle(self) -> None:
        """Verify full lifecycle: proposal -> signed approval -> UWG commit -> receipt -> rollback."""
        proposal = create_calibration_proposal(
            proposal_type="hitl_confidence",
            target_parameter="hitl.bayesian_prior_strength",
            current_value=4.0,
            proposed_value=7.5,
            problem_description="Override cohort in silence_gap_classification requires higher prior strength",
            run_ids=["run-test-uwg-1"],
            evidence_event_ids=["evt-uwg-1"],
            evaluation_plan=["replay_decisions_brier_score"],
            risk="medium",
        )
        validate_artifact(proposal)

        # Create authentic human signed approval receipt
        approval_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-hitl-lead",
            rationale="Approved calibrated Bayesian prior strength 7.5 under Brier score improvement",
            origin="USER_EXPLICIT",
        )

        commit_request = {
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": approval_receipt,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
        }

        # Execute commit through Universal Write Gateway
        result = self.uwg.process_commit_request(commit_request)

        self.assertEqual(result["status"], "APPLIED")
        self.assertIn("commit_id", result)
        self.assertEqual(result["graph_delta_counts"], {"node": 0, "assertion": 0, "evidence_item": 0, "total_delta": 0})

        # Validate that apply receipt complies with contract
        apply_receipt = result["receipt"]
        validate_artifact(apply_receipt)
        self.assertEqual(apply_receipt["status"], "APPLIED")
        self.assertEqual(apply_receipt["applied_parameters"], {"hitl.bayesian_prior_strength": 7.5})
        self.assertEqual(apply_receipt["previous_parameters"], {"hitl.bayesian_prior_strength": 4.0})

        # Verify config file updated on disk
        reg = ParameterRegistry(config_path=self.config_path)
        reg.clear_cache()
        self.assertEqual(reg.get_float("hitl.bayesian_prior_strength"), 7.5)

        # Verify rollback receipt exists and execute rollback
        rollback_receipt_path = result["rollback_receipt_path"]
        self.assertTrue(Path(rollback_receipt_path).is_file())

        rollback_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_rollback",
            "rollback_path": rollback_receipt_path,
        })

        self.assertEqual(rollback_res["status"], "ROLLED_BACK")

        # Verify parameter reverted back to 4.0
        reg.clear_cache()
        self.assertEqual(reg.get_float("hitl.bayesian_prior_strength"), 4.0)

        # Verify apply receipt status updated to ROLLED_BACK
        apply_on_disk = read_json(Path(result["receipt_path"]))
        self.assertEqual(apply_on_disk["status"], "ROLLED_BACK")


if __name__ == "__main__":
    unittest.main()
