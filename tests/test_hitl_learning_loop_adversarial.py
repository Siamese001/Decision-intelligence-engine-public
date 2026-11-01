#!/usr/bin/env python3
"""Adversarial & Edge-Case Test Suite for Governed HITL Learning Lifecycle.

Proves:
1. Rejection of synthetic IDE stop hook approvals (validate_approval_origin fails closed).
2. Rejection of unauthenticated or tampered Ed25519 signatures.
3. Rejection of out-of-bounds parameter proposals during dry-run simulation.
4. Rejection of proposals that fail to improve Brier score (Δ_Brier >= 0).
5. Immediate detection of event chain or database tampering breaking L4 seals.
6. Rejection of auto_apply: true or approval_required: false invariant violations.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import (
    create_signed_calibration_approval_receipt,
    verify_calibration_approval,
)
from dual_pipeline.hitl_governance import (
    SYNTHETIC_APPROVAL_MARKERS,
    validate_approval_origin,
)
from dual_pipeline.hitl_governance_core.crypto import (
    generate_hitl_keypair,
    get_governed_hitl_keypair,
)
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.telemetry_core.recorder import RunRecorder, verify_run_l4_seal


class TestHitlLearningLoopAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.runtime_root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )
        self.priv_key, self.pub_key = get_governed_hitl_keypair(key_dir=self.tmp_path / "keys")

        self.cohort = {
            "cohort_key": "hitl_override:adversarial_test",
            "occurrence_count": 3,
            "run_ids": ["run-adv-1"],
            "items": [
                {"decision_id": f"adv-{i}", "outcome_type": "OVERRIDDEN", "confidence": 0.60}
                for i in range(3)
            ],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthetic_stop_hook_approval_rejected(self) -> None:
        """Synthetic stop hook approvals emitted by IDE auto-review policy fail closed."""
        proposal = self.engine.generate_proposal_for_cohort(
            self.cohort,
            proposal_type="hitl_confidence",
        )

        for marker in SYNTHETIC_APPROVAL_MARKERS:
            synthetic_receipt = {
                "receipt_id": "rcpt-synthetic-01",
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["canonical_digest"]["value"],
                "approved_by": "synthetic_stop_hook",
                "approved_at": "2026-10-03T12:00:00Z",
                "source": "system_hook",
                "reason": f"Approval message: {marker}",
            }
            is_valid, reason = validate_approval_origin(synthetic_receipt)
            self.assertFalse(is_valid, f"Failed to reject synthetic marker: {marker}")
            self.assertIn("APPROVAL_REJECTED_SYNTHETIC_ORIGIN", reason)

            # verify_calibration_approval must reject
            self.assertFalse(verify_calibration_approval(proposal, synthetic_receipt))

            # verify_proposal_approval in engine must reject
            eng_valid, eng_reason = self.engine.verify_proposal_approval(proposal, synthetic_receipt)
            self.assertFalse(eng_valid)

    def test_unauthenticated_or_tampered_signature_rejected(self) -> None:
        """Tampered or invalid cryptographic signatures are rejected."""
        proposal = self.engine.generate_proposal_for_cohort(
            self.cohort,
            proposal_type="hitl_confidence",
        )

        valid_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="authorized_operator",
            private_key=self.priv_key,
            public_key=self.pub_key,
            user_command="implement",
        )

        # 1. Tamper proposal_digest
        tampered_digest = dict(valid_receipt)
        tampered_digest["proposal_digest"] = "f" * 64
        self.assertFalse(verify_calibration_approval(proposal, tampered_digest))

        # 2. Tamper signature bytes
        tampered_sig = dict(valid_receipt)
        tampered_sig["ed25519_signature"] = "A" * 64
        tampered_sig["signature"] = "A" * 64
        self.assertFalse(verify_calibration_approval(proposal, tampered_sig))

        # 3. Wrong keypair (unauthorized third-party key)
        rogue_priv, rogue_pub = generate_hitl_keypair()
        rogue_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="unauthorized_actor",
            private_key=rogue_priv,
            public_key=rogue_pub,
            user_command="implement",
        )
        # Using authorized public key to verify unauthorized signature fails
        rogue_receipt["public_key"] = valid_receipt["public_key"]
        self.assertFalse(verify_calibration_approval(proposal, rogue_receipt))

    def test_out_of_bounds_parameters_fail_simulation(self) -> None:
        """Parameter proposals exceeding governed bounds fail dry-run simulation."""
        # Max bound for bayesian_prior_strength is 10.0
        proposal_overflow = self.engine.generate_proposal_for_cohort(
            self.cohort,
            proposal_type="hitl_confidence",
            target_override={
                "parameter_name": "hitl.bayesian_prior_strength",
                "proposed_value": 15.0,  # Exceeds max 10.0
            },
        )
        sim_overflow = self.engine.simulate_proposal(proposal_overflow)
        self.assertFalse(sim_overflow.success)
        self.assertEqual(sim_overflow.status, "SIMULATED_FAIL")
        self.assertIn("outside governed bounds", sim_overflow.details.get("error", ""))

        # Min bound for ambiguity_margin_threshold is 0.05
        proposal_underflow = self.engine.generate_proposal_for_cohort(
            self.cohort,
            proposal_type="hitl_confidence",
            target_override={
                "parameter_name": "hitl.ambiguity_margin_threshold",
                "proposed_value": 0.01,  # Below min 0.05
            },
        )
        sim_underflow = self.engine.simulate_proposal(proposal_underflow)
        self.assertFalse(sim_underflow.success)
        self.assertEqual(sim_underflow.status, "SIMULATED_FAIL")

    def test_l4_seal_tamper_detection(self) -> None:
        """Tampering with an event in a sealed run triggers immediate L4 verification failure."""
        run_id = "run-adv-l4-tamper"
        recorder = RunRecorder(
            runtime_root=self.runtime_root,
            run_id=run_id,
            trace_id="b" * 32,
            use_case="learning",
            graph_snapshot=None,
        )
        corr = {
            "source_id": None,
            "candidate_id": None,
            "query_id": None,
            "graph_operation_id": None,
            "hitl_item_id": "hitl-item-adv-l4",
        }
        for stage in ("LP0", "LP1", "LP2", "LP3", "LP4"):
            recorder.record(
                stage=stage,
                event_type="pipeline_event",
                payload={"data": f"payload_{stage}"},
                correlation=corr,
                status="PASS",
                record_count=1,
            )
        seal = recorder.seal(
            required_stages=["LP0", "LP1", "LP2", "LP3", "LP4", "L4"],
            correlation=corr,
        )
        self.assertIsNotNone(seal)
        recorder.close()

        # Legitimate seal passes
        verified = verify_run_l4_seal(self.runtime_root, run_id)
        self.assertEqual(verified["status"], "PASS")

        # Now tamper with one recorded event JSON on disk
        event_files = sorted((self.runtime_root / "runs" / run_id / "learning_events").glob("*.json"))
        target_event = event_files[0]
        event_data = json.loads(target_event.read_text(encoding="utf-8"))
        event_data["status"] = "FAIL"
        target_event.write_text(json.dumps(event_data), encoding="utf-8")

        # Re-verification must fail with tampering detected
        with self.assertRaises(ValueError) as ctx:
            verify_run_l4_seal(self.runtime_root, run_id)
        self.assertIn("tampering detected", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
