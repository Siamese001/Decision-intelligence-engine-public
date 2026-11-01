"""Adversarial and edge-case qualification suite for Wave 1.

Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty traces, 0-step inputs, null record sets.
2. Malformed input: Citation flooding, empty responses, semantic disconnections.
3. Invariant bypass attempt: Missing optimal path baselines, changed-argument terminal failures.
4. Rollback and recovery: Unhandled terminal errors invalidating prior adaptation credits.
"""
from __future__ import annotations

from unittest import TestCase

from dual_pipeline.evals.answer_eval_core.judge import (
    evaluate_with_semantic_judge,
    evaluate_with_structural_heuristic,
)
from dual_pipeline.evals.trajectory.evaluator import TrajectoryEvaluator
from dual_pipeline.evals.trajectory.models import TrajectoryStep, TrajectoryTrace


class TestAgenticEvidenceAdversarial(TestCase):
    """Adversarial testing suite for Wave 1 baseline evaluation boundaries."""

    def test_category_boundary_payload_empty_inputs(self) -> None:
        """Category: Boundary payload - handles 0-step traces and empty records fail-closed."""
        # 1. Empty trajectory trace
        evaluator = TrajectoryEvaluator()
        empty_trace = TrajectoryTrace(
            trace_id="tr-empty-001",
            goal="Empty trace boundary test",
            pipeline_id="pipeline_b",
            steps=[],
            optimal_step_count=None,
        )
        card = evaluator.evaluate_trace(empty_trace)
        self.assertFalse(card.passed)
        self.assertFalse(card.terminal_task_success)
        self.assertFalse(card.optimal_steps_available)
        self.assertEqual(card.total_steps, 0)
        self.assertEqual(card.efficiency_ratio, 0.0)

        # 2. Empty answer records
        empty_heur = evaluate_with_structural_heuristic([])
        self.assertEqual(empty_heur.evaluated_count, 0)
        self.assertEqual(empty_heur.evaluator_kind, "structural_heuristic")
        self.assertFalse(empty_heur.semantic_validation_passed)

        empty_sem = evaluate_with_semantic_judge([])
        self.assertEqual(empty_sem.evaluated_count, 0)
        self.assertEqual(empty_sem.evaluator_kind, "semantic_judge")
        self.assertFalse(empty_sem.semantic_validation_passed)

    def test_category_malformed_citation_flooded_irrelevance(self) -> None:
        """Category: Malformed input - semantic judge detects irrelevant text despite valid-looking citations."""
        hostile_records = [
            {
                "query": "Explain cloud migration architecture for Apex Global.",
                "response_text": "Bake an apple pie at 350 degrees for forty-five minutes with cinnamon.",
                "cited_assertion_ids": ["ast-101", "ast-102"],  # Two valid citations attached to irrelevant text
                "is_irrelevant_adversarial_sample": True,
            }
        ]

        # Structural heuristic returns high score because len(cited) >= 2, but labels itself honestly
        heur_score = evaluate_with_structural_heuristic(hostile_records)
        self.assertEqual(heur_score.evaluator_kind, "structural_heuristic")
        self.assertGreaterEqual(heur_score.average_relevance_score, 0.75)
        self.assertFalse(heur_score.semantic_validation_passed)

        # Semantic judge rejects the irrelevant text and sets semantic_validation_passed=False
        sem_score = evaluate_with_semantic_judge(hostile_records)
        self.assertEqual(sem_score.evaluator_kind, "semantic_judge")
        self.assertFalse(sem_score.semantic_validation_passed)
        self.assertLessEqual(sem_score.average_relevance_score, 0.20)

    def test_category_invariant_bypass_missing_optimal_steps(self) -> None:
        """Category: Invariant bypass attempt - unknown optimal path cannot earn qualified success."""
        evaluator = TrajectoryEvaluator()
        trace_without_optimal = TrajectoryTrace(
            trace_id="tr-bypass-001",
            goal="Attempt efficiency pass without optimal step baseline",
            pipeline_id="pipeline_c",
            steps=[
                TrajectoryStep(step_index=1, actor_id="agent:test", action_type="tool_call", tool_name="fetch", status="SUCCESS"),
                TrajectoryStep(step_index=2, actor_id="agent:test", action_type="final_response", status="SUCCESS"),
            ],
            optimal_step_count=None,  # Unknown optimal path
        )

        card = evaluator.evaluate_trace(trace_without_optimal)
        self.assertFalse(card.passed, "Must fail closed when optimal steps are unknown")
        self.assertFalse(card.optimal_steps_available)
        self.assertEqual(card.efficiency_ratio, 0.0, "Efficiency cannot default to 1.0 when optimal is unknown")
        self.assertIn("Unknown optimal path", " ".join(card.details["gate_failures"]))

    def test_category_rollback_recovery_changed_argument_terminal_failure(self) -> None:
        """Category: Rollback & recovery - changing arguments into another failure is NOT a recovery."""
        evaluator = TrajectoryEvaluator()
        # Step 1 errors, Step 2 changes arguments but ALSO fails (terminal failure)
        trace_failed_retry = TrajectoryTrace(
            trace_id="tr-recovery-fail-001",
            goal="Changed argument failure regression",
            pipeline_id="pipeline_d",
            steps=[
                TrajectoryStep(step_index=1, actor_id="agent:test", action_type="tool_call", tool_name="query", tool_arguments={"q": "v1"}, status="ERROR"),
                TrajectoryStep(step_index=2, actor_id="agent:test", action_type="tool_call", tool_name="query", tool_arguments={"q": "v2"}, status="ERROR"),
            ],
            optimal_step_count=2,
        )

        card = evaluator.evaluate_trace(trace_failed_retry)
        self.assertFalse(card.passed)
        self.assertFalse(card.terminal_task_success)
        self.assertEqual(card.adaptation_attempts, 1, "One adaptation attempt occurred")
        self.assertEqual(card.successful_recoveries, 0, "Zero successful recoveries occurred")
        self.assertEqual(card.details["unresolved_error_count"], 2)
        self.assertLessEqual(card.error_recovery_rate, 0.40)
        self.assertIn("Terminal task failure", " ".join(card.details["gate_failures"]))
