"""Adversarial and edge-case test suite for Wave 12: Integrated Agentic Program & Rollout.

Covers 6 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty workflow records, empty scenario dicts, zero-latency inputs.
2. Malformed input: Corrupted locks, unparseable budget records.
3. Concurrency & race: Multi-threaded parallel promotion evaluations and rollbacks.
4. Overflow & exhaustion: Large batch evaluation (thousands of scenarios), massive budgets.
5. Invariant bypass: Direct promotion without locks, guardrail override attempts.
6. Rollback & recovery: Emergency abort from broad cohort to disabled state.
"""
from __future__ import annotations

import concurrent.futures
import unittest
from typing import Any, Dict

from dual_pipeline.rollout_controls_core import (
    CrossPipelineEvaluationReport,
    CrossPipelineEvaluator,
    RolloutDecision,
    RolloutGate,
    RolloutTier,
)

ALL_PREDECESSOR_LOCKS = [f"wave_{i}.complete" for i in range(1, 12)]


class AgenticProgramAdversarialTests(unittest.TestCase):
    """Adversarial and security test cases for Wave 12 program qualification."""

    def setUp(self) -> None:
        self.evaluator = CrossPipelineEvaluator()
        self.gate = RolloutGate()

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_boundary_01_empty_workflow_records_handled_safely(self) -> None:
        """Empty workflow records list returns safe non-eligible report without exception."""
        report = self.evaluator.evaluate_workflows([])
        self.assertFalse(report.all_workflows_passed)
        self.assertFalse(report.is_rollout_eligible)
        self.assertEqual(report.aggregate_latency_ms, 0.0)

    def test_boundary_02_empty_scenarios_dict_handled(self) -> None:
        """Workflow record with zero scenarios evaluates without zero-division error."""
        records = [{"workflow_id": "WF-EMPTY", "scenarios": {}}]
        report = self.evaluator.evaluate_workflows(records)
        self.assertFalse(report.all_workflows_passed)
        self.assertEqual(report.accuracy_ratio, 0.0)

    # --- CATEGORY 2: MALFORMED INPUTS ---

    def test_malformed_01_corrupted_lock_items_ignored(self) -> None:
        """Lock list with None or malformed strings does not satisfy required predecessor check."""
        corrupted_locks = [None, "", "not_a_wave_lock", 12345]  # type: ignore[list-item]
        decision = self.gate.evaluate_promotion(
            surface_id="surface-test",
            current_tier=RolloutTier.ISOLATED_QUALIFICATION,
            target_tier=RolloutTier.NARROW_OPERATOR_PILOT,
            predecessor_locks=corrupted_locks,  # type: ignore[arg-type]
            operator_approved=True,
        )
        self.assertFalse(decision.approved)
        self.assertFalse(decision.predecessor_locks_verified)

    def test_malformed_02_negative_latency_or_cost_normalized(self) -> None:
        """Negative latency or cost metrics evaluate without throwing exceptions."""
        records = [
            {
                "workflow_id": "WF-ANOMALY",
                "latency_ms": -50.0,
                "cost_usd": -0.01,
                "scenarios": {"check": {"success": True}},
            }
        ]
        report = self.evaluator.evaluate_workflows(records)
        self.assertTrue(report.all_workflows_passed)
        self.assertEqual(report.accuracy_ratio, 1.0)

    # --- CATEGORY 3: CONCURRENCY & RACE CONDITIONS ---

    def test_concurrency_01_parallel_promotions_and_rollbacks(self) -> None:
        """Concurrent evaluation and rollbacks across multiple threads maintain gate integrity."""
        def worker_task(worker_id: int) -> bool:
            surface = f"surface-{worker_id % 4}"
            # Promote
            d1 = self.gate.evaluate_promotion(
                surface_id=surface,
                current_tier=RolloutTier.ISOLATED_QUALIFICATION,
                target_tier=RolloutTier.NARROW_OPERATOR_PILOT,
                predecessor_locks=ALL_PREDECESSOR_LOCKS,
                operator_approved=True,
            )
            # Rollback
            d2 = self.gate.rollback_to_tier(
                surface_id=surface,
                target_tier=RolloutTier.ISOLATED_QUALIFICATION,
                reason="concurrency stress test",
            )
            return d1.approved and d2.approved

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker_task, i) for i in range(20)]
            results = [f.result() for f in futures]

        self.assertTrue(all(results))
        self.assertEqual(len(results), 20)

    # --- CATEGORY 4: OVERFLOW & EXHAUSTION ---

    def test_overflow_01_large_scenario_batch_evaluation(self) -> None:
        """Evaluating thousands of scenarios completes efficiently without recursion limits."""
        massive_scenarios = {f"test_{i}": {"success": True} for i in range(1000)}
        records = [
            {
                "workflow_id": "WF-MASSIVE",
                "latency_ms": 100.0,
                "cost_usd": 0.001,
                "scenarios": massive_scenarios,
            }
        ]
        report = self.evaluator.evaluate_workflows(records)
        self.assertTrue(report.all_workflows_passed)
        self.assertEqual(report.accuracy_ratio, 1.0)

    def test_overflow_02_massive_budget_bounds(self) -> None:
        """Extreme latency and cost values correctly trip fail-closed budget checks."""
        records = [
            {
                "workflow_id": "WF-EXPLODED",
                "latency_ms": 999_999.0,
                "cost_usd": 1000.0,
                "scenarios": {"run": {"success": True}},
            }
        ]
        report = self.evaluator.evaluate_workflows(records)
        self.assertFalse(report.is_rollout_eligible)
        self.assertFalse(report.all_workflows_passed)

    # --- CATEGORY 5: INVARIANT BYPASS ATTEMPTS ---

    def test_bypass_01_fake_locks_rejected(self) -> None:
        """Supplying spoofed lock names fails predecessor verification."""
        fake_locks = [f"fake_lock_{i}.complete" for i in range(1, 12)]
        decision = self.gate.evaluate_promotion(
            surface_id="surface-fake",
            current_tier=RolloutTier.ISOLATED_QUALIFICATION,
            target_tier=RolloutTier.NARROW_OPERATOR_PILOT,
            predecessor_locks=fake_locks,
            operator_approved=True,
        )
        self.assertFalse(decision.approved)
        self.assertFalse(decision.predecessor_locks_verified)

    def test_bypass_02_guardrail_violation_overrides_operator_approval(self) -> None:
        """Operator approval cannot override detected guardrail violations."""
        guardrail_failure = {
            "is_compliant": False,
            "violations": ["Critical egress data leakage pattern detected in test run."],
        }
        decision = self.gate.evaluate_promotion(
            surface_id="surface-guardrail",
            current_tier=RolloutTier.ISOLATED_QUALIFICATION,
            target_tier=RolloutTier.NARROW_OPERATOR_PILOT,
            predecessor_locks=ALL_PREDECESSOR_LOCKS,
            operator_approved=True,  # Operator approved, but guardrail failed
            guardrail_report=guardrail_failure,
        )
        self.assertFalse(decision.approved)
        self.assertIn("GUARDRAIL_VIOLATIONS", decision.rationale)

    # --- CATEGORY 6: ROLLBACK & RECOVERY ---

    def test_recovery_01_emergency_abort_to_disabled(self) -> None:
        """Emergency rollback directly to DISABLED state takes immediate effect."""
        # Setup surface at broad cohort
        self.gate.evaluate_promotion(
            surface_id="surface-prod",
            current_tier=RolloutTier.ISOLATED_QUALIFICATION,
            target_tier=RolloutTier.BROAD_APPROVED_COHORT,
            predecessor_locks=ALL_PREDECESSOR_LOCKS,
            operator_approved=True,
        )
        self.assertEqual(self.gate.get_surface_tier("surface-prod"), RolloutTier.BROAD_APPROVED_COHORT)

        # Emergency abort
        abort = self.gate.rollback_to_tier(
            surface_id="surface-prod",
            target_tier=RolloutTier.DISABLED,
            reason="EMERGENCY_KILLSWITCH: Discovered regression in live feed",
        )
        self.assertTrue(abort.approved)
        self.assertEqual(self.gate.get_surface_tier("surface-prod"), RolloutTier.DISABLED)
