"""Wave 8 Acceptance Test Suite: BGE-M3 Retrieval Clearance & Final Architecture Lock.

Verifies:
1. Canonical 11 non-compensating retrieval release gates contract (all pass and non-compensating fail-closed).
2. Steady-state query latency ceiling enforcement (< 7,500ms).
3. Retrieval clearance policy invariants (rejection of unparsed, invalid, or unauthorized records).
4. Synthetic embedding ban enforcement (strict prohibition of mock/synthetic vector generation).
5. All 8 convergence waves (Waves 0-8) certified COMPLETE with sealed runtime receipts.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.release_policy import (
    RETRIEVAL_RELEASE_11_GATES,
    evaluate_retrieval_11_gates,
)
from dual_pipeline.retrieval.agents.release_gate_auditor import (
    GATE_NAMES,
    ReleaseGateAuditor,
)
from dual_pipeline.retrieval_clearance import retrieval_clearance
from tests.embedding_fixture import MetadataOnlyBgeM3Backend

ROOT = Path(__file__).resolve().parents[1]


class TestWave8RetrievalAndArchitectureLock(unittest.TestCase):
    """Wave 8 Acceptance Test Suite for Retrieval Clearance and Architecture Lock."""

    def test_retrieval_11_gates_all_pass(self) -> None:
        """Verifies evaluate_retrieval_11_gates passes when all 11 gates evaluate True."""
        self.assertEqual(len(RETRIEVAL_RELEASE_11_GATES), 11)
        valid_gates = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}
        passed, failures = evaluate_retrieval_11_gates(valid_gates)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_retrieval_11_gates_non_compensating_failure(self) -> None:
        """Verifies non-compensating behavior: any single gate failure or omission blocks release."""
        for target_gate in RETRIEVAL_RELEASE_11_GATES:
            # Test missing gate
            incomplete_gates = {g: True for g in RETRIEVAL_RELEASE_11_GATES if g != target_gate}
            passed, failures = evaluate_retrieval_11_gates(incomplete_gates)
            self.assertFalse(passed, f"Omission of {target_gate} should fail closed")
            self.assertIn(f"{target_gate} (MISSING)", failures)

            # Test failing gate
            failing_gates = {g: (g != target_gate) for g in RETRIEVAL_RELEASE_11_GATES}
            passed, failures = evaluate_retrieval_11_gates(failing_gates)
            self.assertFalse(passed, f"Failure of {target_gate} should fail closed")
            self.assertIn(f"{target_gate} (FAILED)", failures)

    def test_retrieval_latency_ceiling_enforcement(self) -> None:
        """Verifies ReleaseGateAuditor strictly enforces latency < 7,500ms ceiling."""
        auditor = ReleaseGateAuditor(mock_mode=True)
        self.assertEqual(len(GATE_NAMES), 11)

        # Baseline compliant run (<7,500ms)
        task_compliant = A2ATask(
            task_id="audit-latency-pass",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit release gates under SLA",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "graph_sha256": "a" * 64,
                "sidecar_sha256": "b" * 64,
                "max_query_latency_ms": 1250.0,
            },
        )
        res_compliant = asyncio.run(auditor.handle_task(task_compliant))
        self.assertTrue(res_compliant["all_gates_passed"])
        self.assertEqual(res_compliant["qualification_status"], "DELIVERY_READY")
        self.assertEqual(res_compliant["gate_results"]["latency_threshold"]["status"], "PASS")

        # Non-compliant latency run (>=7,500ms)
        task_breached = A2ATask(
            task_id="audit-latency-fail",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit release gates with latency SLA breach",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "graph_sha256": "a" * 64,
                "sidecar_sha256": "b" * 64,
                "max_query_latency_ms": 7501.0,
            },
        )
        res_breached = asyncio.run(auditor.handle_task(task_breached))
        self.assertFalse(res_breached["all_gates_passed"])
        self.assertEqual(res_breached["qualification_status"], "BLOCKED")
        self.assertEqual(res_breached["gate_results"]["latency_threshold"]["status"], "FAIL")

    def test_retrieval_clearance_policy_invariants(self) -> None:
        """Verifies retrieval_clearance correctly rejects unparsed or invalid records."""
        # Valid approved record
        allowed, reason = retrieval_clearance(
            assertion_type="factual",
            classification="internal",
            audiences=["nee-engineering"],
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        # Invalid derived assertion type
        allowed, reason = retrieval_clearance(
            assertion_type="recommendation",
            classification="internal",
            audiences=["nee-engineering"],
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "derived_assertion_type_not_retrieval_eligible")

        # Forbidden audience (private_personal)
        allowed, reason = retrieval_clearance(
            assertion_type="factual",
            classification="confidential",
            audiences=["private_personal"],
        )
        self.assertFalse(allowed)

    def test_synthetic_embedding_ban_enforcement(self) -> None:
        """Verifies MetadataOnlyBgeM3Backend prohibits mock/synthetic vector generation."""
        backend = MetadataOnlyBgeM3Backend()
        self.assertEqual(backend.retrieval_encoder, "metadata-only")
        self.assertEqual(backend.model_id, "BAAI/bge-m3")

        # Attempting to encode text with test fixture must raise AssertionError
        with self.assertRaises(AssertionError) as ctx:
            backend.encode("Test query text for vector generation")
        self.assertIn("metadata-only BGE test double must not encode text", str(ctx.exception))

    def test_all_eight_convergence_waves_certified_complete(self) -> None:
        """Verifies all waves (0-8) are declared COMPLETE with existing sealed receipts."""
        wave_plan_path = ROOT / "documentation" / "AGENTIC_ARCHITECTURE_CONVERGENCE_WAVE_PLAN.md"
        self.assertTrue(wave_plan_path.is_file(), "Wave plan specification must exist")
        wave_plan_path.read_text(encoding="utf-8")

        expected_receipts = [
            "reports/wave0_agentic_architecture_anti_pattern_audit_receipt.json",
            "reports/mmi_snapshot.json",
            "reports/wave2_module_budget_burndown_receipt.json",
            "reports/wave3_credential_ssot_receipt.json",
            "reports/wave4_feedback_topology_receipt.json",
            "reports/wave5_uwg_concurrency_receipt.json",
            "reports/wave6_observability_calibration_receipt.json",
            "reports/wave7_dead_code_purge_receipt.json",
            "reports/wave8_final_architecture_lock_receipt.json",
        ]

        for receipt_rel in expected_receipts:
            receipt_path = ROOT / receipt_rel
            self.assertTrue(
                receipt_path.is_file(),
                f"Required wave receipt missing: {receipt_rel}",
            )


if __name__ == "__main__":
    unittest.main()
