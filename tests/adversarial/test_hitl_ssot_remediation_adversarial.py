"""
tests/adversarial/test_hitl_ssot_remediation_adversarial.py

Adversarial Verification Suite for HITL SSOT Remediation.
Covers:
1. Invariant Bypass Attempts:
   - High confidence (0.99 vs 0.01) evading Lock 2 on mandatory HITL categories.
   - Synthetic identity injection ('auto_approver', 'synthetic_stop_hook') attempting automated bypass.
   - Substring collision evasion ('do not approve', 'reject approval', 'cannot approve').
2. Malformed Inputs:
   - Options lacking explicit confidence when require_confidence=True (anti-fabrication).
   - Corrupted option confidence structures.
3. Replay & Duplicate Resume:
   - Duplicate clearance dispatch idempotency.
   - Attempting to replay or resume an already processed approval request.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.hitl_governance_core.classifier import DecisionCategory
from dual_pipeline.hitl_governance_core.canonical_evaluator import (
    evaluate_canonical_decision,
    extract_canonical_option_confidence,
)
from dual_pipeline.hitl_governance_core.surfacing import HitlConfidenceMissingError
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    validate_approval_response_signature,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    _classify_mutation_selection,
)
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)


class TestHitlSsotRemediationAdversarial(unittest.TestCase):
    """Adversarial stress testing for unified HITL governance and SSOT rules."""

    def test_invariant_bypass_high_confidence_lock2_protected_category(self) -> None:
        """High confidence spread (0.99 vs 0.01) CANNOT bypass Lock 2 on protected categories."""
        options = [
            {"label": "Architectural Schema Migration", "confidence_score": 0.99, "tier": "HIGH"},
            {"label": "Cancel Migration", "confidence_score": 0.01, "tier": "LOW"},
        ]
        # Category is ARCHITECTURAL (mandatory HITL)
        res = evaluate_canonical_decision(
            options,
            category=DecisionCategory.ARCHITECTURAL,
            enable_lock2=True,
        )
        self.assertTrue(res.lock1_passed, "Lock 1 math spread passes")
        self.assertFalse(res.lock2_passed, "Lock 2 must reject autonomous resolution")
        self.assertTrue(res.surface_hitl, "Mandatory HITL must be enforced")
        self.assertFalse(res.autonomous_permitted, "Autonomous resolution strictly barred")
        self.assertIsNotNone(res.ask_user_question_payload)

    def test_invariant_bypass_synthetic_origin_rejected(self) -> None:
        """Synthetic origin injection ('auto_approver', 'synthetic_stop_hook') fails closed."""
        for bad_origin in ("auto_approver", "synthetic_stop_hook", "mock_operator", "simulated_operator"):
            with self.subTest(origin=bad_origin):
                is_valid, reason = validate_approval_response_signature(
                    response={
                        "selected_option": "Approve Mutation",
                        "operator_id": "operator_valid",
                        "origin": bad_origin,
                    },
                    allowed_options=["Approve Mutation", "Reject Mutation"],
                )
                self.assertFalse(is_valid)
                self.assertIn("SYNTHETIC_APPROVAL_REJECTED", reason)

    def test_invariant_bypass_substring_collision_negation(self) -> None:
        """Negated responses like 'do not approve' or 'reject approval' must never classify as APPROVE."""
        negated_inputs = [
            "do not approve",
            "Do Not Approve",
            "DO NOT APPROVE",
            "I do not approve this change",
            "never approve",
            "reject approval",
            "cannot approve at this time",
            "will not approve",
        ]
        for user_text in negated_inputs:
            with self.subTest(text=user_text):
                mutation_class = _classify_mutation_selection(user_text)
                self.assertNotEqual(
                    mutation_class,
                    "APPROVE",
                    f"'{user_text}' must NOT classify as APPROVE",
                )

    def test_malformed_input_missing_confidence_fails_closed(self) -> None:
        """Options lacking explicit confidence strictly raise HitlConfidenceMissingError (no fabrication)."""
        options_no_conf = [
            {"label": "Option Alpha"},
            {"label": "Option Beta"},
        ]
        # In canonical evaluator with require_confidence=True (default in surfacing)
        with self.assertRaises(HitlConfidenceMissingError):
            extract_canonical_option_confidence(options_no_conf[0], require_confidence=True)

        with self.assertRaises(HitlConfidenceMissingError):
            evaluate_canonical_decision(options_no_conf, require_confidence=True)

    def test_malformed_input_corrupted_confidence_types(self) -> None:
        """Non-numeric confidence scores fail closed and reject autonomous bypass."""
        corrupted_options = [
            {"label": "Corrupted Alpha", "confidence_score": "not-a-number", "tier": "HIGH"},
            {"label": "Corrupted Beta", "confidence_score": None, "tier": "LOW"},
        ]
        # Fail-closed: must surface HITL
        res = evaluate_canonical_decision(corrupted_options, require_confidence=False)
        self.assertTrue(res.surface_hitl)
        self.assertFalse(res.autonomous_permitted)

    def test_duplicate_resume_and_clearance_idempotency(self) -> None:
        """Cleared decisions avoid duplicate prompts and resume idempotently without state corruption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            ledger_path = temp_path / "hitl_decision_ledger.json"
            runtime_root = temp_path / "runtime"
            runtime_root.mkdir(parents=True, exist_ok=True)

            run_id = "run_adv_dup_001"
            directive_id = "dir_adv_dup_001"
            delta_digest = "sha256:delta_adv_dup_001"

            req = build_active_ingestion_approval_request(
                run_id=run_id,
                directive_id=directive_id,
                runtime_root=str(runtime_root),
                project="adversarial_test",
                source_package_digest="sha256:pkg_adv_dup_001",
                proposed_delta_digest=delta_digest,
                baseline_generation=1,
            )

            # 1. Dispatch with approved mock transport
            appr_option = req["question_payload"]["options"][0]["label"]
            mock_transport = MagicMock(return_value={"selected_option": appr_option, "operator_id": "operator_sec"})

            disp1 = dispatch_and_persist_approval_request(
                req,
                transport=mock_transport,
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertEqual(disp1.get("response_status"), "APPROVED")
            self.assertEqual(mock_transport.call_count, 1)

            # 2. Resuming with approval
            resumed1, rmsg1, updated1 = resume_ingestion_with_approval(
                request_id=req["request_id"],
                operator_response={"selected_option": appr_option, "operator_id": "operator_sec"},
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertTrue(resumed1)
            self.assertTrue("AUTHORIZED" in rmsg1 or "CLEARANCE_ISSUED" in rmsg1)

            # 3. Second resume of same cleared request -> idempotent restoration without re-persisting
            resumed2, rmsg2, updated2 = resume_ingestion_with_approval(
                request_id=req["request_id"],
                operator_response={"selected_option": appr_option, "operator_id": "operator_sec"},
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertTrue(resumed2)
            self.assertIn("AUTHORIZED", rmsg2)

            # 4. Dispatch again with same directive_id and delta -> returns existing clearance without calling transport!
            mock_transport2 = MagicMock()
            disp2 = dispatch_and_persist_approval_request(
                req,
                transport=mock_transport2,
                storage_root=runtime_root,
                ledger_path=ledger_path,
            )
            self.assertEqual(disp2.get("response_status"), "APPROVED")
            self.assertEqual(mock_transport2.call_count, 0, "UI prompt must be skipped due to prior clearance")


if __name__ == "__main__":
    unittest.main()
