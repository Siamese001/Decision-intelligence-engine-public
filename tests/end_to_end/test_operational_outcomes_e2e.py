"""End-to-end lifecycle verification for Operational Outcomes and Incident Diagnosis (Wave 11).

Verifies:
1. UI completion/stage/activation labels derive from actual run and independent state readback.
2. Fixture markers never count as real audits.
3. Errors, pending, and partial states remain visible and are never masked as completed.
4. Recurring failures produce deterministic incident signatures and source-linked diagnostic proposals.
5. Diagnosis engine is strictly read-only: cannot mutate active sessions, auto-commit, or merge.
6. Explicit operator feedback remains cryptographically bound to authentic delivered results.
7. Scheduled task adapter preserves error/partial visibility without false completion flags.
8. Metrics accurately reflect recurrence, rework, and zero false completions.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.operational_outcomes_core import (
    AdapterState,
    DiagnosticAction,
    IncidentDiagnosisEngine,
    OperationalOutcomeAdapter,
    ScheduledTaskOutcomeHandler,
)


class TestOperationalOutcomesE2E(unittest.TestCase):
    """End-to-end qualification suite for Wave 11 operational outcome connections."""

    def setUp(self) -> None:
        self.adapter = OperationalOutcomeAdapter(run_id="run-wave11-e2e")
        self.diagnosis_engine = IncidentDiagnosisEngine()
        self.scheduled_handler = ScheduledTaskOutcomeHandler(
            adapter=self.adapter,
            diagnosis_engine=self.diagnosis_engine,
        )

    def test_ui_completion_requires_independent_readback_e2e(self) -> None:
        """UI completion state strictly requires independent persistent state readback."""
        run_ctx = {
            "run_id": "run_001",
            "status": "COMPLETED",  # Declared self-status
            "expected_stages": ["U0", "L1", "L0", "C0", "L2"],
        }
        # Without independent readback -> UNVERIFIED
        res_no_readback = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=None)
        self.assertEqual(res_no_readback.state, AdapterState.UNVERIFIED)
        self.assertFalse(res_no_readback.is_authentic_audit)

        # With independent readback confirming on-disk state and all stages -> COMPLETED
        readback_valid = {
            "verified_on_disk": True,
            "completed_stages": ["U0", "L1", "L0", "C0", "L2"],
            "has_errors": False,
        }
        res_valid = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=readback_valid)
        self.assertEqual(res_valid.state, AdapterState.COMPLETED)
        self.assertTrue(res_valid.is_authentic_audit)

    def test_fixture_markers_rejected_as_real_audits_e2e(self) -> None:
        """Synthetic fixture or mock audit markers are rejected from qualifying real audits."""
        for marker_key in ("mock_audit", "is_fixture", "synthetic_fixture"):
            run_ctx = {
                "run_id": f"run_mock_{marker_key}",
                marker_key: True,
                "expected_stages": ["U0", "L1"],
            }
            readback = {"verified_on_disk": True, "completed_stages": ["U0", "L1"]}
            res = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=readback)
            self.assertEqual(res.state, AdapterState.UNVERIFIED)
            self.assertFalse(res.is_authentic_audit)
            self.assertIn("REJECTED_FIXTURE_MARKER", res.rationale)

    def test_adapter_errors_and_partial_states_remain_visible_e2e(self) -> None:
        """Errors and partial lifecycle stages remain fully visible and never report as completed."""
        run_ctx = {
            "run_id": "run_partial",
            "expected_stages": ["U0", "L1", "L0", "C0", "L2"],
        }
        # Incomplete stages -> PARTIAL
        readback_partial = {
            "verified_on_disk": True,
            "completed_stages": ["U0", "L1"],
            "has_errors": False,
        }
        res_partial = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=readback_partial)
        self.assertEqual(res_partial.state, AdapterState.PARTIAL)
        self.assertIn("L0", res_partial.missing_stages)
        self.assertIn("C0", res_partial.missing_stages)

        # Terminal error -> ERROR
        readback_error = {
            "has_errors": True,
            "error_message": "SQLITE_BUSY: database lock contention observed",
        }
        res_error = self.adapter.derive_lifecycle_state(run_ctx, independent_readback=readback_error)
        self.assertEqual(res_error.state, AdapterState.ERROR)
        self.assertIn("SQLITE_BUSY", res_error.details.get("error", ""))

    def test_recurring_failure_generates_source_linked_diagnostic_proposal_e2e(self) -> None:
        """Recurring incident generates deterministic signature, tracks recurrence, and bounds fix."""
        failure_ctx = {
            "category": "retrieval_failure",
            "error_code": "EMPTY_CANDIDATE_POOL",
            "source_component": "dual_pipeline.query_core",
            "root_cause": "Hybrid index query returned zero candidates for sparse BM25 segment.",
            "source_file": "dual_pipeline/query_core/retrieval.py",
            "line_number": 142,
            "suggested_test_scope": ["tests/test_retrieval_coverage.py"],
            "evidence_refs": ["receipt:retrieval_001"],
        }

        # First failure observation
        prop1 = self.diagnosis_engine.diagnose_incident(failure_ctx)
        self.assertEqual(prop1.bounded_action, DiagnosticAction.SUGGEST_RETRIEVAL_REINDEX)
        self.assertEqual(prop1.line_number, 142)
        self.assertTrue(prop1.is_read_only)
        self.assertIn("recurrence: 1", prop1.explanation)

        # Second failure observation (recurring)
        prop2 = self.diagnosis_engine.diagnose_incident(failure_ctx)
        self.assertEqual(prop1.signature_hash, prop2.signature_hash)
        self.assertIn("recurrence: 2", prop2.explanation)

    def test_read_only_diagnosis_blocks_mutation_and_merge_e2e(self) -> None:
        """Diagnosis engine fails closed when asked to execute mutations or merges."""
        failure_ctx = {
            "category": "schema_violation",
            "error_code": "MISSING_REQUIRED_KEY",
            "source_component": "contracts.validator",
            "root_cause": "Field 'directive_id' missing from intake envelope.",
        }
        proposal = self.diagnosis_engine.diagnose_incident(failure_ctx)

        with self.assertRaises(PermissionError) as ctx:
            self.diagnosis_engine.execute_action(proposal, action_name="auto_git_merge")
        self.assertIn("READ_ONLY_INVARIANT_VIOLATION", str(ctx.exception))

        with self.assertRaises(PermissionError) as ctx2:
            self.diagnosis_engine.execute_action(proposal, action_name="mutate_active_graph")
        self.assertIn("READ_ONLY_INVARIANT_VIOLATION", str(ctx2.exception))

    def test_explicit_feedback_linked_to_delivered_result_e2e(self) -> None:
        """Explicit feedback binds strictly to verified delivery result and receipt digest."""
        verified_receipts = {
            "result_delivery_42": "sha256:aabbcc112233445566778899aabbcc112233445566778899aabbcc1122334455",
        }
        feedback_payload = {"rating": 5, "comment": "Excellent attribution accuracy."}

        # Matching digest binds successfully
        bound = self.adapter.bind_delivered_feedback(
            result_id="result_delivery_42",
            receipt_digest="sha256:aabbcc112233445566778899aabbcc112233445566778899aabbcc1122334455",
            feedback_payload=feedback_payload,
            verified_receipts_index=verified_receipts,
        )
        self.assertEqual(bound["status"], "BOUND")
        self.assertEqual(bound["result_id"], "result_delivery_42")

        # Forged or mismatched digest fails closed
        with self.assertRaises(ValueError) as ctx:
            self.adapter.bind_delivered_feedback(
                result_id="result_delivery_42",
                receipt_digest="sha256:forged_digest_000000000000000000000000000000000000000000000000",
                feedback_payload=feedback_payload,
                verified_receipts_index=verified_receipts,
            )
        self.assertIn("RECEIPT_DIGEST_MISMATCH", str(ctx.exception))

    def test_scheduled_task_outcome_preserves_error_visibility_e2e(self) -> None:
        """Scheduled task handler preserves full error visibility and attaches diagnosis."""
        task_ctx = {
            "category": "scheduled_scout_timeout",
            "error_code": "LOCK_TIMEOUT",
            "run_id": "run_scout_99",
            "line_number": 88,
        }
        readback_error = {
            "has_errors": True,
            "error_message": "WAL lock timeout during scheduled scout.",
        }

        report = self.scheduled_handler.process_task_outcome(
            task_id="task_scout_nightly",
            task_name="graph_scout",
            execution_context=task_ctx,
            independent_readback=readback_error,
        )
        self.assertEqual(report.state, AdapterState.ERROR)
        self.assertTrue(report.is_visible)
        self.assertIsNotNone(report.diagnostic_proposal)
        assert report.diagnostic_proposal is not None
        self.assertEqual(report.diagnostic_proposal.bounded_action, DiagnosticAction.SUGGEST_ISOLATED_REPLAY)

    def test_operational_metrics_recurrence_and_zero_false_completion_e2e(self) -> None:
        """Operational metrics report authentic recurrence, rework, and 0.0 false completion."""
        failure_ctx = {
            "category": "concurrency_lock",
            "error_code": "BUSY_TIMEOUT",
            "root_cause": "Database connection exceeded 30000ms busy timeout.",
        }
        # Trigger same incident 3 times
        for _ in range(3):
            self.diagnosis_engine.diagnose_incident(failure_ctx)

        self.diagnosis_engine.record_operator_rework()

        metrics = self.diagnosis_engine.get_metrics()
        self.assertEqual(metrics["total_incidents"], 3)
        self.assertEqual(metrics["unique_signatures"], 1)
        self.assertEqual(metrics["recurrent_incidents_count"], 2)  # 3 - 1 = 2 recurrences
        self.assertEqual(metrics["operator_rework_count"], 1)
        self.assertEqual(metrics["false_completion_rate"], 0.0)
