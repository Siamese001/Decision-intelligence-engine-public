"""
tests/end_to_end/test_hitl_ssot_remediation_e2e.py

End-to-End Lifecycle Verification Suite for HITL SSOT Remediation.
Tests:
1. Full Decision Pipeline:
   - Evaluator -> Question Builder -> Layman Formatting -> Dispatch -> Operator Selection -> Ledger Record -> Ingestion Resume.
2. Anti-Fabrication E2E:
   - Realistic options with explicit confidence scores and tiers are rendered without distortion.
3. Clearance Persistence & Idempotent Readback:
   - Verified that approved decisions are stored in the persistent Merkle-backed ledger and prevent repeated interactive questioning.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.hitl_governance_core.classifier import DecisionCategory
from dual_pipeline.hitl_governance_core.canonical_evaluator import (
    evaluate_canonical_decision,
)
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    validate_approval_response_signature,
)


class TestHitlSsotRemediationE2E(unittest.TestCase):
    """E2E lifecycle tests verifying unified HITL decision governance across the pipeline."""

    def test_full_ambiguity_gating_and_question_dispatch_lifecycle(self) -> None:
        """Top-two confidence gap <= 30% generates compliant ask_user_question payload and executes cleanly."""
        options = [
            {
                "label": "Canonical Graph Re-Index",
                "confidence_score": 0.72,
                "tier": "MEDIUM",
                "plain_english_summary": "Rebuild lexical and dense indexes across canonical nodes.",
                "impact_statement": "Ensures complete query recall.",
            },
            {
                "label": "Partial Incremental Sync",
                "confidence_score": 0.58,
                "tier": "MEDIUM",
                "plain_english_summary": "Update only mutated delta nodes.",
                "impact_statement": "Reduces indexing time by 80%.",
            },
        ]
        # Spread is 0.72 - 0.58 = 0.14 <= 0.30 -> Ambiguous decision requires HITL
        res = evaluate_canonical_decision(options, enable_lock2=False)
        self.assertTrue(res.surface_hitl)
        self.assertFalse(res.autonomous_permitted)
        self.assertIsNotNone(res.ask_user_question_payload)

        payload = res.ask_user_question_payload
        self.assertIn("questions", payload)
        self.assertEqual(len(payload["questions"]), 1)
        q = payload["questions"][0]
        self.assertEqual(len(q["options"]), 2)

        # First option must have (Recommended) prefix and explicit confidence
        opt0 = q["options"][0]
        self.assertTrue(opt0.startswith("(Recommended) "))
        self.assertIn("72%", opt0)
        self.assertIn("MEDIUM", opt0)

        # Second option must not have (Recommended) prefix
        opt1 = q["options"][1]
        self.assertFalse(opt1.startswith("(Recommended) "))
        self.assertIn("58%", opt1)
        self.assertIn("MEDIUM", opt1)

    def test_active_ingestion_approval_e2e_lifecycle(self) -> None:
        """Complete active ingestion approval lifecycle from request creation through ledger verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            ledger_path = temp_path / "hitl_decision_ledger.json"
            runtime_root = temp_path / "runtime"
            runtime_root.mkdir(parents=True, exist_ok=True)

            run_id = "run-e2e-hitl-001"
            directive_id = "dir-e2e-hitl-001"
            delta_digest = "sha256:delta_e2e_hitl_001"

            # 1. Build governed active ingestion approval request
            req = build_active_ingestion_approval_request(
                run_id=run_id,
                directive_id=directive_id,
                runtime_root=str(runtime_root),
                project="e2e_remediation_test",
                source_package_digest="sha256:pkg_e2e_hitl_001",
                proposed_delta_digest=delta_digest,
                baseline_generation=2,
                permitted_operation_class="candidate_publication",
            )
            self.assertEqual(req["contract_id"], "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0")
            self.assertEqual(req["dispatch_status"], "PENDING")
            self.assertEqual(req["response_status"], "AWAITING")

            # 2. Check clearance before approval -> must be False
            cleared_before, _, _ = check_active_ingestion_clearance(
                run_id=run_id,
                directive_id=directive_id,
                delta_digest=delta_digest,
                ledger_path=ledger_path,
            )
            self.assertFalse(cleared_before)

            # 3. Simulate interactive transport where operator selects option 0
            opt_label = req["question_payload"]["options"][0]["label"]
            mock_transport = MagicMock(return_value={
                "selected_option": opt_label,
                "operator_id": "operator_chief",
                "origin": "operator_ui",
            })

            # Dispatch request
            dispatched = dispatch_and_persist_approval_request(
                req,
                transport=mock_transport,
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertEqual(dispatched["response_status"], "APPROVED")
            self.assertEqual(mock_transport.call_count, 1)

            # 4. Resume ingestion
            resumed, msg, final_req = resume_ingestion_with_approval(
                request_id=req["request_id"],
                operator_response={
                    "selected_option": opt_label,
                    "operator_id": "operator_chief",
                    "origin": "operator_ui",
                },
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertTrue(resumed)
            self.assertEqual(final_req["response_status"], "APPROVED")

            # 5. Verify clearance in persistent ledger
            cleared_after, cl_msg, rec = check_active_ingestion_clearance(
                run_id=run_id,
                directive_id=directive_id,
                delta_digest=delta_digest,
                ledger_path=ledger_path,
            )
            self.assertTrue(cleared_after)
            self.assertIsNotNone(rec)

            # 6. Verify subsequent dispatch skips UI and reuses prior clearance
            mock_transport2 = MagicMock()
            dispatched2 = dispatch_and_persist_approval_request(
                req,
                transport=mock_transport2,
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertEqual(dispatched2["response_status"], "APPROVED")
            self.assertEqual(mock_transport2.call_count, 0)


if __name__ == "__main__":
    unittest.main()
