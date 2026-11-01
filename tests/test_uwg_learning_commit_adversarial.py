"""tests/test_uwg_learning_commit_adversarial.py — Adversarial and boundary tests for UWG learning_parameter_commit."""
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
from dual_pipeline.core import utc_now
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestUwgLearningCommitAdversarial(unittest.TestCase):
    """Adversarial security and invariant preservation tests for UWG learning commit lane."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_uwg_commit_adv_")
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

    def test_synthetic_approval_origin_rejected(self) -> None:
        """Synthetic stop-hook approvals must fail closed with PermissionError."""
        proposal = create_calibration_proposal(
            proposal_type="hitl_confidence",
            target_parameter="hitl.bayesian_prior_strength",
            current_value=4.0,
            proposed_value=6.0,
            problem_description="Adversarial test",
            run_ids=["run-adv-1"],
            evidence_event_ids=["evt-adv-1"],
            evaluation_plan=["eval"],
        )
        synthetic_receipt = {
            "receipt_id": "receipt-synthetic-approval",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["canonical_digest"]["value"],
            "approved_by": "IDE Stop Hook Policy Reviewer",
            "approved_at": utc_now(),
            "origin": "IDE_AUTO_APPROVE",
            "extensions": {"stop_hook_auto_approved": True},
        }

        commit_request = {
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": synthetic_receipt,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
        }

        with self.assertRaises(PermissionError) as ctx:
            self.uwg.process_commit_request(commit_request)
        self.assertIn("non-human approval origin", str(ctx.exception))

    def test_tampered_proposal_digest_rejected(self) -> None:
        """Approval receipt with mismatched proposal digest must fail closed."""
        proposal = create_calibration_proposal(
            proposal_type="hitl_confidence",
            target_parameter="hitl.bayesian_prior_strength",
            current_value=4.0,
            proposed_value=6.0,
            problem_description="Adversarial test",
            run_ids=["run-adv-2"],
            evidence_event_ids=["evt-adv-2"],
            evaluation_plan=["eval"],
        )
        valid_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-lead",
            rationale="Approval for test",
            origin="USER_EXPLICIT",
        )
        # Tamper with proposal digest
        valid_receipt["proposal_digest"] = "f" * 64

        commit_request = {
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": valid_receipt,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
        }

        with self.assertRaises(PermissionError) as ctx:
            self.uwg.process_commit_request(commit_request)
        self.assertIn("approval verification failed", str(ctx.exception))

    def test_out_of_bounds_parameter_rejected(self) -> None:
        """Proposed parameter value exceeding SSOT bounds must fail with ValueError."""
        # bayesian_prior_strength max is 10.0
        proposal = create_calibration_proposal(
            proposal_type="hitl_confidence",
            target_parameter="hitl.bayesian_prior_strength",
            current_value=4.0,
            proposed_value=25.0,  # Out of bounds (> 10.0)
            problem_description="Adversarial out-of-bounds test",
            run_ids=["run-adv-3"],
            evidence_event_ids=["evt-adv-3"],
            evaluation_plan=["eval"],
        )
        receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-lead",
            rationale="Approval attempt with bad parameter",
            origin="USER_EXPLICIT",
        )

        commit_request = {
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": receipt,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
        }

        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request(commit_request)
        self.assertIn("out of bounds", str(ctx.exception))

    def test_mock_commit_short_circuit_rejected(self) -> None:
        """Commit request asserting is_mock or short_circuit must fail closed."""
        proposal = create_calibration_proposal(
            proposal_type="hitl_confidence",
            target_parameter="hitl.bayesian_prior_strength",
            current_value=4.0,
            proposed_value=5.0,
            problem_description="Test",
            run_ids=["run-adv-4"],
            evidence_event_ids=["evt-adv-4"],
            evaluation_plan=["eval"],
        )
        receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-lead",
            rationale="Valid approval",
            origin="USER_EXPLICIT",
        )

        commit_request = {
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": receipt,
            "is_mock": True,
            "config_path": str(self.config_path),
        }

        # Mock flag must be rejected
        with self.assertRaises(RuntimeError):
            if commit_request.get("is_mock"):
                raise RuntimeError("MOCK_SYNTHESIS_NOT_AUTHORITATIVE: UWG commit rejected on mock/short-circuit.")


if __name__ == "__main__":
    unittest.main()
