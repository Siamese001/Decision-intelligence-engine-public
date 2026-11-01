"""
tests/end_to_end/test_active_ingestion_approval_e2e.py

End-to-End Governance Test Suite for Active Graph Ingestion Approval Lifecycle (Wave 2).
Exercises the complete approval lifecycle:
  1. Discovery of unapproved active ingestion scope.
  2. Governed request generation via build_active_ingestion_approval_request.
  3. Interactive question dispatch and display receipt persistence.
  4. Authentic operator response processing and Merkle-linked ledger recording.
  5. Subsequent clearance checks and idempotent re-use without re-prompting.
  6. Scope invalidation when proposed delta digest changes.
  7. Explicit operator decline lifecycle and rejection persistence.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.core import digest, read_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    discover_runtime_capabilities,
    dispatch_interactive_question,
)


class ActiveIngestionApprovalE2ETests(unittest.TestCase):
    """End-to-end suite verifying active ingestion approval request, dispatch, and resumption."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.ledger_path = self.temp_path / "hitl_decision_ledger.json"
        self.sqlite_path = self.temp_path / "hitl_decision_ledger.sqlite"
        self.runtime_root = self.temp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_active_ingestion_approval_lifecycle(self) -> None:
        """Verify full lifecycle: unapproved -> request -> dispatch -> resume -> clearance -> idempotency."""
        run_id = "run-e2e-101"
        directive_id = "dir-e2e-202"
        delta_digest = "sha256:delta_sample_abc123"
        pkg_digest = "sha256:pkg_sample_def456"

        # 1. Unapproved scope check
        is_cleared, msg, rec = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)
        self.assertIn("UNRATIFIED", msg)
        self.assertIsNone(rec)

        # 2. Build request
        req = build_active_ingestion_approval_request(
            run_id=run_id,
            directive_id=directive_id,
            runtime_root=str(self.runtime_root),
            project="test_proj",
            source_package_digest=pkg_digest,
            proposed_delta_digest=delta_digest,
            baseline_generation=1,
            permitted_operation_class="candidate_publication",
            table_deltas={"nodes": 5, "edges": 3, "assertions": 8},
        )
        self.assertEqual(req["contract_id"], "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0")
        self.assertEqual(req["run_id"], run_id)
        self.assertEqual(req["proposed_delta_digest"], delta_digest)
        self.assertEqual(req["dispatch_status"], "PENDING")

        # 3. Dispatch without immediate response (interactive wait)
        dispatched_req = dispatch_and_persist_approval_request(
            req,
            transport=lambda payload: {"status": "DISPATCHED", "tool": "ask_user_question"},
            ledger_path=self.ledger_path,
            storage_root=self.runtime_root,
        )
        self.assertEqual(dispatched_req["dispatch_status"], "DISPATCHED")
        self.assertIn("display_receipt", dispatched_req["extensions"])
        req_file = self.runtime_root / "approval_requests" / f"{req['request_id']}.json"
        self.assertTrue(req_file.is_file())

        # Verify still unapproved while awaiting response
        is_cleared, msg, _ = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)

        # 4. Resume with authentic operator approval response
        appr_option = req["question_payload"]["options"][0]["label"]
        operator_response = {
            "status": "SUCCESS",
            "selected_option": appr_option,
            "operator_id": "operator_alice",
            "rationale": "Verified candidate publication package and schema deltas",
        }
        resumed, r_msg, updated_req = resume_ingestion_with_approval(
            request_id=req["request_id"],
            operator_response=operator_response,
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(resumed)
        self.assertIn("AUTHORIZED", r_msg)
        self.assertEqual(updated_req["response_status"], "APPROVED")
        self.assertTrue(updated_req["authentic_response"]["is_approved"])
        self.assertIsNotNone(updated_req["authentic_response"]["entry_digest"])

        # 5. Check clearance now succeeds
        is_cleared, msg, rec = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(is_cleared)
        self.assertIn("RATIFIED", msg)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["operator_id"], "operator_alice")

        # 6. Idempotency: second check does not duplicate decisions
        is_cleared_again, _, _ = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(is_cleared_again)

        # 7. Scope change: if proposed delta changes, clearance must fail
        different_delta = "sha256:delta_different_999"
        is_cleared_diff, diff_msg, _ = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=different_delta,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared_diff)
        self.assertIn("UNRATIFIED", diff_msg)

    def test_operator_decline_lifecycle(self) -> None:
        """Verify explicit operator decline records decline and blocks clearance."""
        run_id = "run-decline-01"
        directive_id = "dir-decline-01"
        delta_digest = "sha256:delta_decline_01"

        req = build_active_ingestion_approval_request(
            run_id=run_id,
            directive_id=directive_id,
            runtime_root=str(self.runtime_root),
            project="test_proj",
            source_package_digest="sha256:pkg_decline",
            proposed_delta_digest=delta_digest,
            baseline_generation=1,
            table_deltas={"nodes": 3, "assertions": 2},
        )

        dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )

        decline_option = req["question_payload"]["options"][1]["label"]
        operator_response = {
            "status": "SUCCESS",
            "selected_option": decline_option,
            "operator_id": "operator_bob",
            "rationale": "Decline candidate publication due to unverified source provenance",
        }
        resumed, r_msg, updated_req = resume_ingestion_with_approval(
            request_id=req["request_id"],
            operator_response=operator_response,
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(resumed)
        self.assertIn("DECLINED", r_msg)
        self.assertEqual(updated_req["response_status"], "DECLINED")

        # Clearance check must return False with explicit DECLINED status
        is_cleared, msg, rec = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)
        self.assertIn("DECLINED", msg)
        self.assertIsNotNone(rec)

    def test_immediate_dispatch_transport_response(self) -> None:
        """Verify transport that provides immediate human response executes end-to-end in one call."""
        run_id = "run-immediate-01"
        directive_id = "dir-immediate-01"
        delta_digest = "sha256:delta_imm_01"

        req = build_active_ingestion_approval_request(
            run_id=run_id,
            directive_id=directive_id,
            runtime_root=str(self.runtime_root),
            project="test_proj",
            source_package_digest="sha256:pkg_imm",
            proposed_delta_digest=delta_digest,
            baseline_generation=2,
            table_deltas={"nodes": 4, "assertions": 6},
        )

        opt_approve = req["question_payload"]["options"][0]["label"]

        def mock_immediate_transport(payload: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "status": "COMPLETED",
                "result": {
                    "status": "SUCCESS",
                    "selected_option": opt_approve,
                    "operator_id": "operator_carol",
                    "rationale": "Approved immediately via interactive prompt",
                },
            }

        sealed_res = dispatch_and_persist_approval_request(
            req,
            transport=mock_immediate_transport,
            ledger_path=self.ledger_path,
            storage_root=self.runtime_root,
        )

        self.assertEqual(sealed_res["response_status"], "APPROVED")
        self.assertTrue(sealed_res["authentic_response"]["is_approved"])

        is_cleared, msg, rec = check_active_ingestion_clearance(
            run_id=run_id,
            directive_id=directive_id,
            delta_digest=delta_digest,
            ledger_path=self.ledger_path,
        )
        self.assertTrue(is_cleared)
        self.assertIn("RATIFIED", msg)


if __name__ == "__main__":
    unittest.main()
