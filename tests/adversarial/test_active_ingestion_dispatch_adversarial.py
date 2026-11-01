"""
tests/adversarial/test_active_ingestion_dispatch_adversarial.py

Adversarial and edge-case testing for Wave 5 Active Ingestion Dispatch & Lifecycle:
  1. Expired approval request rejection on resume (boundary / expiration).
  2. Delivery failure tracking and retry budget exhaustion (exhaustion / overflow).
  3. Duplicate notification prevention (idempotency / race prevention).
  4. Tampered / invalid signature rejection (integrity / malformed input).
  5. Explicit operator decline handling and non-mutation enforcement.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.scheduler_adapter import (
    check_duplicate_delivery,
    get_pending_approval_task,
    record_delivery_attempt,
)


class TestActiveIngestionDispatchAdversarial(unittest.TestCase):
    """Adversarial test suite verifying resilience of approval dispatch and lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.ledger_path = self.tmp_path / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_expired_request_rejected_on_resume(self) -> None:
        """Verify an expired request cannot be approved or resumed (boundary / TTL)."""
        req = build_active_ingestion_approval_request(
            run_id="run-exp-01",
            directive_id="dir-exp-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="1" * 64,
            proposed_delta_digest="2" * 64,
            baseline_generation=1,
            ttl_hours=-1,  # Expired in past
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        approved_option = req["question_payload"]["options"][0]["label"]
        auth_response = {
            "operator_id": "human_operator",
            "selected_option": approved_option,
            "signature": f"sig:{approved_option}:human_operator",
            "rationale": "Attempting to approve expired request",
        }

        authorized, msg, updated = resume_ingestion_with_approval(
            request_id=req_id,
            operator_response=auth_response,
            storage_root=self.tmp_path,
            ledger_path=self.ledger_path,
        )

        self.assertFalse(authorized)
        self.assertIn("EXPIRED", msg)
        self.assertEqual(updated["response_status"], "EXPIRED")

    def test_adversarial_delivery_failure_tracks_exhaustion(self) -> None:
        """Verify repeated delivery failures exhaust retry budget and transition to DELIVERY_EXHAUSTED."""
        req = build_active_ingestion_approval_request(
            run_id="run-fail-01",
            directive_id="dir-fail-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="3" * 64,
            proposed_delta_digest="4" * 64,
            baseline_generation=1,
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        # Attempt 1: Fail
        r1 = record_delivery_attempt(req_id, self.tmp_path, success=False, error="HTTP 503 Service Unavailable", max_retries=3)
        self.assertEqual(r1["dispatch_status"], "RETRY_PENDING")

        # Attempt 2: Fail
        r2 = record_delivery_attempt(req_id, self.tmp_path, success=False, error="Connection timeout", max_retries=3)
        self.assertEqual(r2["dispatch_status"], "RETRY_PENDING")

        # Attempt 3: Fail -> Exhausted
        r3 = record_delivery_attempt(req_id, self.tmp_path, success=False, error="Endpoint unreachable", max_retries=3)
        self.assertEqual(r3["dispatch_status"], "DELIVERY_EXHAUSTED")
        self.assertTrue(r3["extensions"]["delivery"]["delivery_exhausted"])

    def test_adversarial_duplicate_notification_prevention(self) -> None:
        """Verify check_duplicate_delivery prevents duplicate notifications across invocations."""
        req = build_active_ingestion_approval_request(
            run_id="run-dup-01",
            directive_id="dir-dup-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="5" * 64,
            proposed_delta_digest="6" * 64,
            baseline_generation=1,
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        self.assertFalse(check_duplicate_delivery(req_id, "slack_alerts", self.tmp_path))

        # Record a successful delivery to channel
        record_delivery_attempt(req_id, self.tmp_path, success=True, target_channel="slack_alerts")

        self.assertTrue(check_duplicate_delivery(req_id, "slack_alerts", self.tmp_path))
        self.assertFalse(check_duplicate_delivery(req_id, "email_alerts", self.tmp_path))

    def test_adversarial_tampered_signature_rejected(self) -> None:
        """Verify resume rejects tampered or mismatched signature payloads."""
        req = build_active_ingestion_approval_request(
            run_id="run-tamper-01",
            directive_id="dir-tamper-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="7" * 64,
            proposed_delta_digest="8" * 64,
            baseline_generation=1,
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        tampered_response = {
            "operator_id": "synthetic_auto_approval",
            "selected_option": "Approve candidate publication into active knowledge graph",
            "metadata": {"approval_origin": "synthetic_stop_hook_auto_approval"},
            "signature": "invalid_unauthenticated_signature",
            "rationale": "Forged approval",
        }

        authorized, msg, _ = resume_ingestion_with_approval(
            request_id=req_id,
            operator_response=tampered_response,
            storage_root=self.tmp_path,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(authorized)
        self.assertIn("SYNTHETIC", msg)

    def test_adversarial_operator_decline_halts_publication(self) -> None:
        """Verify operator decline marks DECLINED and refuses publication authorization."""
        req = build_active_ingestion_approval_request(
            run_id="run-dec-01",
            directive_id="dir-dec-01",
            runtime_root=str(self.tmp_path),
            project="project_nexus",
            source_package_digest="9" * 64,
            proposed_delta_digest="a" * 64,
            baseline_generation=1,
        )
        dispatched = dispatch_and_persist_approval_request(req, storage_root=self.tmp_path)
        req_id = dispatched["request_id"]

        decline_option = req["question_payload"]["options"][1]["label"]
        decline_response = {
            "operator_id": "human_operator",
            "selected_option": decline_option,
            "signature": f"sig:{decline_option}:human_operator",
            "rationale": "Sensitive data detected; declining publication",
        }

        authorized, msg, updated = resume_ingestion_with_approval(
            request_id=req_id,
            operator_response=decline_response,
            storage_root=self.tmp_path,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(authorized)
        self.assertIn("DECLINED", msg)
        self.assertEqual(updated["response_status"], "DECLINED")


if __name__ == "__main__":
    unittest.main()
