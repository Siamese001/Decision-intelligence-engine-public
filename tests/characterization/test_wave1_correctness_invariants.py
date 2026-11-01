"""
tests/characterization/test_wave1_correctness_invariants.py

Wave 1 regression test suite protecting P0 correctness and safety invariants:
1. ExtractionCoordinator provider-free boundary and zero unreleased database mutation.
2. AdjudicationJudgeAgent HITL ambiguity margin threshold SSOT (0.25).
3. RosterReconcilerAgent HITL ambiguity margin threshold SSOT (0.25).
4. Evaluator pure evaluation invariant (default auto_heal=False with no artifact mutation side-effects).
"""
from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock

from dual_pipeline.a2a import (
    A2ATask,
    A2ATaskStatus,
    DelegationBudget,
)
from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD
from dual_pipeline.transcript_qa import evaluate_all_qa_gates
from pipeline_a_intake.agents import RosterReconcilerAgent
from pipeline_b_extraction.agents import AdjudicationJudgeAgent, ExtractionCoordinator


class TestWave1CorrectnessInvariants(unittest.IsolatedAsyncioTestCase):
    """Verifies that Wave 1 P0 safety and correctness defects remain resolved."""

    async def test_extraction_coordinator_zero_database_mutation_bypass(self) -> None:
        """ExtractionCoordinator must never mutate SQLite directly; nodes are returned in payload."""
        mock_db = MagicMock()
        coordinator = ExtractionCoordinator(db_manager=mock_db, mock_mode=True)

        # Invariant 1: Agent Card must not claim database mutation tools or skills
        self.assertNotIn("CommitGraphNodes", coordinator.card.skills)
        self.assertNotIn("database_manager", coordinator.card.allowed_tools)
        self.assertIn("a2a_event_broker", coordinator.card.allowed_tools)

        # Invariant 2: Execution must not invoke insert_canonical_node or insert_advisory_node
        task = A2ATask(
            task_id="t-wave1-verify",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Extract from clean transcript snippet",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "evidence_id": "ev-wave1",
                "text": "The engineering team finalized the schema for Project Nexus.",
                "speaker": "Engineer",
            },
            budget=DelegationBudget(timeout_seconds=30.0),
        )

        res = await coordinator.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertIn("canonical_nodes", res)
        self.assertIn("advisory_nodes", res)

        # Direct database write methods must NOT have been called
        mock_db.insert_canonical_node.assert_not_called()
        mock_db.insert_advisory_node.assert_not_called()

    def test_adjudication_judge_default_margin_threshold_ssot(self) -> None:
        """AdjudicationJudgeAgent must use DEFAULT_MARGIN_THRESHOLD (0.25)."""
        judge = AdjudicationJudgeAgent(mock_mode=True)

        # Arbiter margin threshold must match SSOT
        self.assertEqual(judge.hitl_arbiter.margin_threshold, DEFAULT_MARGIN_THRESHOLD)
        self.assertEqual(judge.hitl_arbiter.margin_threshold, 0.25)

        # Inspect arbitrate_disposition signature
        sig = inspect.signature(judge.arbitrate_disposition)
        default_margin = sig.parameters["margin_threshold"].default
        self.assertEqual(default_margin, DEFAULT_MARGIN_THRESHOLD)
        self.assertEqual(default_margin, 0.25)

        # Card domain focus must reference 25% gating
        self.assertIn("25% HITL gating", judge.card.domain_focus)

    def test_roster_reconciler_default_margin_threshold_ssot(self) -> None:
        """RosterReconcilerAgent must use DEFAULT_MARGIN_THRESHOLD (0.25)."""
        reconciler = RosterReconcilerAgent(mock_mode=True)

        # Arbiter margin threshold must match SSOT
        self.assertEqual(reconciler.hitl_arbiter.margin_threshold, DEFAULT_MARGIN_THRESHOLD)
        self.assertEqual(reconciler.hitl_arbiter.margin_threshold, 0.25)

        # Card domain focus must reference 25% gating
        self.assertIn("25% HITL margin gating", reconciler.card.domain_focus)

    def test_evaluator_pure_evaluation_no_side_effects(self) -> None:
        """evaluate_all_qa_gates must default to auto_heal=False with no artifact mutation."""
        sig = inspect.signature(evaluate_all_qa_gates)
        self.assertIn("auto_heal", sig.parameters)
        self.assertIs(sig.parameters["auto_heal"].default, False)

        raw_f1 = "**Date:** 09/10/2026\n[00:00] Meeting start"
        raw_f2 = """# Meeting Recap\n**Date:** 2026-09-10\n## Meeting Objective\nTesting purity."""
        raw_f3 = {"mutations": {}}

        # Default evaluation without auto_heal
        report_default = evaluate_all_qa_gates(
            f1_text=raw_f1,
            f2_text=raw_f2,
            f3_text=raw_f3,
        )
        self.assertNotIn("deterministic_healing", report_default)
        self.assertNotIn("healed_artifacts", report_default)

        # Explicit evaluation with auto_heal=True
        report_healed = evaluate_all_qa_gates(
            f1_text=raw_f1,
            f2_text=raw_f2,
            f3_text=raw_f3,
            auto_heal=True,
        )
        self.assertIn("deterministic_healing", report_healed)
        self.assertIn("healed_artifacts", report_healed)


if __name__ == "__main__":
    unittest.main()
