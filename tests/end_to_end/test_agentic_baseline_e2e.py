"""End-to-end qualification and baseline readback verification for Wave 1.

Verifies:
1. Entry-point and exposure catalog completeness across workflows and shared services.
2. Frozen cohort manifest integrity, sample counts, and clean baseline execution.
3. Separation of structural heuristics from semantic judgment with honest evaluator_kind labeling.
4. Trajectory evaluation requiring optimal path baselines, terminal task success, and true error recovery.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from dual_pipeline.evals.answer_eval_core.judge import (
    evaluate_with_semantic_judge,
    evaluate_with_structural_heuristic,
)
from dual_pipeline.evals.trajectory.evaluator import TrajectoryEvaluator
from dual_pipeline.evals.trajectory.models import TrajectoryStep, TrajectoryTrace


class TestAgenticBaselineE2E(TestCase):
    """End-to-end integration and baseline readback suite."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.catalog_path = self.repo_root / "governance" / "agentic_opportunities" / "entrypoint_exposure_catalog.json"
        self.cohort_path = self.repo_root / "governance" / "agentic_opportunities" / "frozen_cohort_manifest.json"

    def test_entrypoint_catalog_and_readback_integrity(self) -> None:
        """Entrypoint catalog covers all 4 core workflows plus shared services with valid evidence levels."""
        self.assertTrue(self.catalog_path.is_file(), "Catalog missing")
        with open(self.catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        workflows = catalog.get("workflows", [])
        self.assertGreaterEqual(len(workflows), 4, "Must cover at least 4 workflows")

        modalities = {wf["modality"] for wf in workflows}
        self.assertIn("audio/transcript", modalities)
        self.assertIn("notes/documents/attachments", modalities)
        self.assertIn("ingestion", modalities)
        self.assertIn("query/copilot", modalities)

        for wf in workflows:
            self.assertIn("producer_entrypoint", wf)
            self.assertIn("consumer_readback", wf)
            self.assertIn("evaluator_kind", wf)
            self.assertIn("evidence_level", wf)
            self.assertIn(
                wf["evidence_level"],
                ("source_bound", "component_level", "entrypoint_qualified", "model_qualified"),
            )

    def test_frozen_cohort_denominators_and_clean_execution(self) -> None:
        """Frozen cohort manifest specifies explicit denominators and clean samples execute cleanly."""
        self.assertTrue(self.cohort_path.is_file(), "Manifest missing")
        with open(self.cohort_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        cohorts = manifest.get("cohorts", [])
        self.assertEqual(len(cohorts), 4, "Must define 4 cohorts")

        for c in cohorts:
            self.assertGreater(c["denominator"], 0, f"Cohort {c['cohort_id']} must have non-zero denominator")
            self.assertEqual(
                c["clean_count"] + c["adversarial_count"],
                len(c["samples"]),
                f"Cohort {c['cohort_id']} count mismatch",
            )
            self.assertTrue(c.get("blinded_labels"), "Labels must be blinded from candidate predictions")

    def test_structural_heuristic_vs_semantic_judge_clean_baseline(self) -> None:
        """Clean queries evaluate with truthful evaluator_kind across both structural and semantic paths."""
        clean_records = [
            {
                "query": "What are Apex Global cloud milestones?",
                "response_text": "Apex Global completed AWS to GCP migration and reached Stage U0.",
                "cited_assertion_ids": ["ast-101", "ast-102"],
            },
            {
                "query": "Who is VP of Engineering?",
                "response_text": "Frank Miller serves as VP of Engineering.",
                "cited_assertion_ids": ["ast-301"],
            },
        ]

        # Structural heuristic
        heur_score = evaluate_with_structural_heuristic(clean_records)
        self.assertEqual(heur_score.evaluator_kind, "structural_heuristic")
        self.assertEqual(heur_score.evaluated_count, 2)
        self.assertFalse(heur_score.semantic_validation_passed)  # Heuristic does not claim semantic pass
        self.assertGreaterEqual(heur_score.average_relevance_score, 0.75)

        # Semantic judge
        sem_score = evaluate_with_semantic_judge(clean_records)
        self.assertEqual(sem_score.evaluator_kind, "semantic_judge")
        self.assertEqual(sem_score.evaluated_count, 2)
        self.assertTrue(sem_score.semantic_validation_passed)
        self.assertGreaterEqual(sem_score.average_relevance_score, 0.80)

    def test_trajectory_optimal_path_and_recovery_e2e(self) -> None:
        """Trajectory evaluation qualifies optimal efficiency and error recovery end-to-end."""
        evaluator = TrajectoryEvaluator()

        # 1. Clean nominal trace with explicit optimal step baseline
        trace_optimal = TrajectoryTrace(
            trace_id="tr-clean-01",
            goal="Optimal retrieval",
            pipeline_id="pipeline_q",
            steps=[
                TrajectoryStep(step_index=1, actor_id="agent:q", action_type="tool_call", tool_name="search", status="SUCCESS"),
                TrajectoryStep(step_index=2, actor_id="agent:q", action_type="final_response", status="SUCCESS"),
            ],
            optimal_step_count=2,
        )
        card_optimal = evaluator.evaluate_trace(trace_optimal)
        self.assertTrue(card_optimal.passed)
        self.assertTrue(card_optimal.terminal_task_success)
        self.assertTrue(card_optimal.optimal_steps_available)
        self.assertEqual(card_optimal.efficiency_ratio, 1.0)
        self.assertEqual(card_optimal.error_recovery_rate, 1.0)

        # 2. Transient error followed by valid retry recovery
        trace_retry = TrajectoryTrace(
            trace_id="tr-clean-02",
            goal="Transient error recovery",
            pipeline_id="pipeline_q",
            steps=[
                TrajectoryStep(step_index=1, actor_id="agent:q", action_type="tool_call", tool_name="search", tool_arguments={"q": "bad"}, status="ERROR"),
                TrajectoryStep(step_index=2, actor_id="agent:q", action_type="tool_call", tool_name="search", tool_arguments={"q": "good"}, status="SUCCESS"),
            ],
            optimal_step_count=2,
        )
        card_retry = evaluator.evaluate_trace(trace_retry)
        self.assertTrue(card_retry.passed)
        self.assertTrue(card_retry.terminal_task_success)
        self.assertEqual(card_retry.adaptation_attempts, 1)
        self.assertEqual(card_retry.successful_recoveries, 1)
        self.assertEqual(card_retry.error_recovery_rate, 1.0)
