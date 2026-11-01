"""
tests/adversarial/test_active_ingestion_approval_adversarial.py

Adversarial and Edge-Case Test Suite for Active Ingestion Approval (Wave 2).
Covers mandatory edge-case categories:
  1. Malformed input (empty response, invalid types, option mismatch, missing file).
  2. Invariant bypass attempts (synthetic stop-hook forged origins, auto-reviewer strings).
  3. Expiry & boundary conditions (expired approval requests fail closed).
  4. Concurrency & idempotent redelivery (parallel checks and dispatches).
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict

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


class ActiveIngestionApprovalAdversarialTests(unittest.TestCase):
    """Adversarial suite probing approval bypass resistance, malformed inputs, and expiry."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.ledger_path = self.temp_path / "hitl_decision_ledger.json"
        self.runtime_root = self.temp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Category 1: Malformed Input
    def test_malformed_response_types_and_empty_payloads(self) -> None:
        """Malformed response payloads fail closed with explicit diagnostic reasons."""
        allowed = ["Approve mutation", "Decline mutation"]

        # None response
        valid, msg = validate_approval_response_signature(None, allowed)
        self.assertFalse(valid)
        self.assertIn("EMPTY_RESPONSE", msg)

        # Empty string
        valid, msg = validate_approval_response_signature("", allowed)
        self.assertFalse(valid)
        self.assertIn("NO_SELECTION", msg)

        # Whitespace-only string
        valid, msg = validate_approval_response_signature("   ", allowed)
        self.assertFalse(valid)
        self.assertIn("NO_SELECTION", msg)

        # Unsupported type (integer)
        valid, msg = validate_approval_response_signature(12345, allowed)
        self.assertFalse(valid)
        self.assertIn("MALFORMED_RESPONSE", msg)

        # Dictionary with missing selection
        valid, msg = validate_approval_response_signature({"operator_id": "alice"}, allowed)
        self.assertFalse(valid)
        self.assertIn("NO_SELECTION", msg)

    def test_option_mismatch_rejection(self) -> None:
        """Selecting an arbitrary unoffered option string is rejected fail-closed."""
        allowed = ["Approve mutation", "Decline mutation"]
        invalid_choice = "Arbitrary unapproved third option"

        valid, msg = validate_approval_response_signature(invalid_choice, allowed)
        self.assertFalse(valid)
        self.assertIn("OPTION_MISMATCH", msg)

    def test_missing_request_file_raises_file_not_found(self) -> None:
        """Resuming a non-existent approval request raises FileNotFoundError cleanly."""
        with self.assertRaises(FileNotFoundError):
            resume_ingestion_with_approval(
                request_id="req-non-existent-999",
                operator_response={"selected_option": "Approve", "operator_id": "alice"},
                storage_root=self.runtime_root,
                ledger_path=self.ledger_path,
            )

    # Category 2: Invariant Bypass Attempts
    def test_forged_synthetic_stop_hook_response_rejection(self) -> None:
        """Responses originating from synthetic stop-hooks or IDE wrappers are strictly rejected."""
        allowed = ["Approve mutation", "Decline mutation"]

        forged_responses = [
            {"selected_option": "Approve mutation", "operator_id": "synthetic_stop_hook"},
            {
                "selected_option": "Approve mutation",
                "operator_id": "human_alice",
                "metadata": {"approval_origin": "synthetic_stop_hook"},
            },
            {"selected_option": "Approve mutation", "operator_id": "auto_approver"},
        ]

        for forged in forged_responses:
            valid, msg = validate_approval_response_signature(forged, allowed)
            self.assertFalse(valid, f"Expected rejection for {forged}")
            self.assertIn("SYNTHETIC_APPROVAL_REJECTED", msg)

    def test_synthetic_approval_in_ledger_rejected_on_clearance_check(self) -> None:
        """Pre-existing synthetic stop-hook approval in the ledger fails closed on clearance check."""
        store = HITLDecisionStore(ledger_path=self.ledger_path)
        dec_id = "req-synth-bypass-01"
        store.record_decision(
            decision_id=dec_id,
            category="INGESTION_MUTATION",
            prompt="Authorize ingestion?",
            options=[{"key": "Approve"}],
            selected_option={"key": "Approve"},
            operator_id="synthetic_stop_hook",
            metadata={
                "run_id": "run-synth-01",
                "directive_id": "dir-synth-01",
                "delta_digest": "sha256:delta_synth_01",
                "approval_origin": "synthetic_stop_hook",
                "outcome_type": "AFFIRMED",
            },
        )

        is_cleared, msg, _ = check_active_ingestion_clearance(
            run_id="run-synth-01",
            directive_id="dir-synth-01",
            delta_digest="sha256:delta_synth_01",
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)
        self.assertIn("SYNTHETIC_APPROVAL_REJECTED", msg)

    # Category 3: Rollback & Recovery / Expiry
    def test_expired_approval_request_fails_closed(self) -> None:
        """Approval requests past their ttl fail closed with EXPIRED status without ledger mutation."""
        req = build_active_ingestion_approval_request(
            run_id="run-exp-01",
            directive_id="dir-exp-01",
            runtime_root=str(self.runtime_root),
            project="test_proj",
            source_package_digest="sha256:pkg_exp",
            proposed_delta_digest="sha256:delta_exp",
            baseline_generation=1,
            table_deltas={"nodes": 1, "assertions": 1},
            ttl_hours=-1,  # Already expired
        )

        dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )

        appr_option = req["question_payload"]["options"][0]["label"]
        resumed, msg, updated = resume_ingestion_with_approval(
            request_id=req["request_id"],
            operator_response={"selected_option": appr_option, "operator_id": "operator_alice"},
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )

        self.assertFalse(resumed)
        self.assertIn("EXPIRED", msg)
        self.assertEqual(updated["response_status"], "EXPIRED")

        # Verify no approval recorded in ledger
        is_cleared, _, _ = check_active_ingestion_clearance(
            run_id="run-exp-01",
            directive_id="dir-exp-01",
            delta_digest="sha256:delta_exp",
            ledger_path=self.ledger_path,
        )
        self.assertFalse(is_cleared)

    # Category 4: Concurrency & Race
    def test_concurrent_clearance_checks(self) -> None:
        """Concurrent threads querying clearance against the same ledger do not conflict."""
        run_id = "run-conc-01"
        directive_id = "dir-conc-01"
        delta_digest = "sha256:delta_conc_01"

        req = build_active_ingestion_approval_request(
            run_id=run_id,
            directive_id=directive_id,
            runtime_root=str(self.runtime_root),
            project="test_proj",
            source_package_digest="sha256:pkg_conc",
            proposed_delta_digest=delta_digest,
            baseline_generation=1,
            table_deltas={"nodes": 2, "assertions": 1},
        )
        dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )
        appr_option = req["question_payload"]["options"][0]["label"]
        resume_ingestion_with_approval(
            request_id=req["request_id"],
            operator_response={"selected_option": appr_option, "operator_id": "operator_conc"},
            storage_root=self.runtime_root,
            ledger_path=self.ledger_path,
        )

        results = []
        errors = []

        def worker() -> None:
            try:
                cleared, msg, _ = check_active_ingestion_clearance(
                    run_id=run_id,
                    directive_id=directive_id,
                    delta_digest=delta_digest,
                    ledger_path=self.ledger_path,
                )
                results.append((cleared, msg))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(len(results), 8)
        for cleared, msg in results:
            self.assertTrue(cleared)
            self.assertIn("RATIFIED", msg)


if __name__ == "__main__":
    unittest.main()
