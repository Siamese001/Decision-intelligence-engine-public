"""Adversarial and edge-case test suite for Wave 11: Operational Outcomes & Receipts.

Covers 6 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty run contexts, empty/whitespace IDs, None readback.
2. Malformed input: Corrupted line numbers, unparseable failure payloads.
3. Concurrency & race: Multi-threaded parallel diagnosis and feedback binding.
4. Overflow & exhaustion: Recurrence explosion, massive error strings truncation.
5. Invariant bypass: Fixture marker spoofing, mutating actions execution prevention.
6. Rollback & recovery: Clean state preservation after task failures.
"""
from __future__ import annotations

import concurrent.futures
import unittest
from typing import Any, Dict

from dual_pipeline.operational_outcomes_core import (
    AdapterState,
    DiagnosticAction,
    IncidentDiagnosisEngine,
    OperationalOutcomeAdapter,
    ScheduledTaskOutcomeHandler,
)


class OperationalReceiptsAdversarialTests(unittest.TestCase):
    """Adversarial and security test cases for Wave 11 operational outcomes."""

    def setUp(self) -> None:
        self.adapter = OperationalOutcomeAdapter(run_id="run-wave11-adv")
        self.engine = IncidentDiagnosisEngine()
        self.handler = ScheduledTaskOutcomeHandler(
            adapter=self.adapter,
            diagnosis_engine=self.engine,
        )

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_boundary_01_empty_run_context_unverified(self) -> None:
        """Empty dictionary run context defaults safely to UNVERIFIED without exception."""
        res = self.adapter.derive_lifecycle_state({}, independent_readback=None)
        self.assertEqual(res.state, AdapterState.UNVERIFIED)
        self.assertFalse(res.is_authentic_audit)

    def test_boundary_02_empty_result_or_digest_rejected(self) -> None:
        """Empty or whitespace result_id / receipt_digest fail closed on feedback binding."""
        index = {"res_1": "sha256:valid"}
        with self.assertRaises(ValueError):
            self.adapter.bind_delivered_feedback("", "sha256:valid", {}, index)
        with self.assertRaises(ValueError):
            self.adapter.bind_delivered_feedback("res_1", "", {}, index)
        with self.assertRaises(ValueError):
            self.adapter.bind_delivered_feedback("   ", "   ", {}, index)

    def test_boundary_03_none_root_cause_handled_gracefully(self) -> None:
        """None or missing root cause does not crash diagnosis engine."""
        proposal = self.engine.diagnose_incident({"category": "misc", "root_cause": None})
        self.assertTrue(proposal.is_read_only)
        self.assertIn("Unspecified operational error", proposal.explanation)

    # --- CATEGORY 2: MALFORMED INPUTS ---

    def test_malformed_01_corrupted_line_number_handled(self) -> None:
        """Non-integer line number is caught defensively and mapped to None."""
        ctx = {
            "category": "syntax",
            "line_number": "not_an_integer_at_all",
        }
        proposal = self.engine.diagnose_incident(ctx)
        self.assertIsNone(proposal.line_number)

    def test_malformed_02_corrupted_payload_missing_keys(self) -> None:
        """Completely empty failure context maps to unclassified incident safely."""
        proposal = self.engine.diagnose_incident({})
        self.assertIsNotNone(proposal.signature_hash)
        self.assertEqual(proposal.bounded_action, DiagnosticAction.DEFER_TO_OPERATOR)

    # --- CATEGORY 3: CONCURRENCY & RACE CONDITIONS ---

    def test_concurrency_01_parallel_diagnosis_and_feedback(self) -> None:
        """Concurrent workers executing diagnosis and feedback binding do not corrupt state."""
        verified_index = {f"res_{i}": f"sha256:digest_{i}" for i in range(20)}

        def worker_task(worker_id: int) -> bool:
            # 1. Diagnose an incident
            prop = self.engine.diagnose_incident({
                "category": f"category_{worker_id % 3}",
                "error_code": "ERR_CONCURRENT",
                "root_cause": "Race condition test",
            })
            if not prop.is_read_only:
                return False

            # 2. Bind feedback
            bound = self.adapter.bind_delivered_feedback(
                result_id=f"res_{worker_id}",
                receipt_digest=f"sha256:digest_{worker_id}",
                feedback_payload={"worker": worker_id},
                verified_receipts_index=verified_index,
            )
            return bound["status"] == "BOUND"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker_task, i) for i in range(20)]
            results = [f.result() for f in futures]

        self.assertTrue(all(results))
        self.assertEqual(len(results), 20)
        metrics = self.engine.get_metrics()
        self.assertEqual(metrics["total_incidents"], 20)

    # --- CATEGORY 4: OVERFLOW & EXHAUSTION ---

    def test_overflow_01_massive_root_cause_string_truncated(self) -> None:
        """Massive 100,000-character root cause string is truncated in normalized signature."""
        huge_cause = "MEMORY_LEAK_TRACE_" * 5000  # 90,000 chars
        proposal = self.engine.diagnose_incident({
            "category": "memory",
            "error_code": "OOM",
            "root_cause": huge_cause,
        })
        self.assertIsNotNone(proposal.signature_hash)
        # Signature hash is compact stable_id ('sig-' + 16 hex chars = 20 chars)
        self.assertEqual(len(proposal.signature_hash), 20)

    def test_overflow_02_high_recurrence_counter(self) -> None:
        """Repeatedly triggering same incident increments recurrence accurately without leak."""
        ctx = {"category": "recurrent_err", "error_code": "E500", "root_cause": "Stable crash"}
        for _ in range(50):
            self.engine.diagnose_incident(ctx)
        metrics = self.engine.get_metrics()
        self.assertEqual(metrics["unique_signatures"], 1)
        self.assertEqual(metrics["recurrent_incidents_count"], 49)

    # --- CATEGORY 5: INVARIANT BYPASS ATTEMPTS ---

    def test_bypass_01_fixture_marker_with_claimed_disk_state_rejected(self) -> None:
        """Attacker supplying fixture marker cannot bypass check even with verified_on_disk=True."""
        run_ctx = {
            "run_id": "run_exploit",
            "is_fixture": True,  # Fixture marker
            "status": "COMPLETED",
        }
        readback = {
            "verified_on_disk": True,
            "completed_stages": ["U0", "L1", "L0", "C0", "L2"],
        }
        res = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=readback)
        self.assertEqual(res.state, AdapterState.UNVERIFIED)
        self.assertFalse(res.is_authentic_audit)

    def test_bypass_02_diagnosis_executing_git_merge_prohibited(self) -> None:
        """Attempting to execute git merge or write operations via diagnosis engine raises PermissionError."""
        proposal = self.engine.diagnose_incident({"category": "git", "error_code": "CONFLICT"})
        for forbidden_action in ("git_merge", "auto_commit", "publish_canonical", "resume_active_session"):
            with self.assertRaises(PermissionError) as ctx:
                self.engine.execute_action(proposal, action_name=forbidden_action)
            self.assertIn("READ_ONLY_INVARIANT_VIOLATION", str(ctx.exception))

    def test_bypass_03_mismatched_receipt_digest_rejected(self) -> None:
        """Attempt to bind feedback to mismatched receipt digest fails closed."""
        index = {"res_valid": "sha256:correct_digest"}
        with self.assertRaises(ValueError) as ctx:
            self.adapter.bind_delivered_feedback(
                result_id="res_valid",
                receipt_digest="sha256:spoofed_digest",
                feedback_payload={"score": 1},
                verified_receipts_index=index,
            )
        self.assertIn("RECEIPT_DIGEST_MISMATCH", str(ctx.exception))

    # --- CATEGORY 6: ROLLBACK & RECOVERY ---

    def test_recovery_01_scheduled_task_failure_preserves_error_state(self) -> None:
        """Task failure cleanly transitions to ERROR state without corrupting subsequent tasks."""
        # 1. Failed task
        rep1 = self.handler.process_task_outcome(
            task_id="task_fail",
            task_name="failing_job",
            execution_context={"error_code": "JOB_CRASH"},
            independent_readback={"has_errors": True, "error_message": "Subprocess exited with 1"},
        )
        self.assertEqual(rep1.state, AdapterState.ERROR)

        # 2. Subsequent healthy task completes without state contamination
        rep2 = self.handler.process_task_outcome(
            task_id="task_ok",
            task_name="healthy_job",
            execution_context={"expected_stages": ["U0"]},
            independent_readback={"verified_on_disk": True, "completed_stages": ["U0"]},
        )
        self.assertEqual(rep2.state, AdapterState.COMPLETED)
