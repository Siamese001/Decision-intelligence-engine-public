"""Adversarial and edge-case test suite for Wave 10: Harm-Directed Graph Maintenance.

Covers 6 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty findings, zero denominator, empty intervals.
2. Malformed input: Inverted/truncated validity intervals, missing assertion keys.
3. Concurrency & race: Multi-threaded isolated candidate replays and simulations.
4. Overflow & exhaustion: Recurrence explosion attacks, out-of-bounds failure rates.
5. Invariant bypass: UWG gate violations, unauthorized promotion, multi-valued bypass.
6. Rollback & recovery: Clean candidate isolation, idempotency of rollback.
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.graph_maintenance_core import (
    CohortEvaluationReport,
    HarmPrioritizedPlanner,
    HygieneFinding,
    IsolatedCandidateReplay,
    MaintenanceOutcomeType,
    QueryImpactRecord,
    are_intervals_disjoint,
    classify_assertion_relationship,
    is_multi_valued_predicate,
)


class KnowledgeMaintenanceAdversarialTests(unittest.TestCase):
    """Adversarial and security test cases for Wave 10 knowledge maintenance."""

    def setUp(self) -> None:
        self.planner = HarmPrioritizedPlanner(run_id="run-wave10-adv")

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_boundary_01_empty_findings_returns_empty_plan(self) -> None:
        """Empty input findings list returns an empty plan with zero harm score."""
        plan = self.planner.plan_maintenance([])
        self.assertEqual(len(plan.proposals), 0)
        self.assertEqual(plan.total_harm_score, 0.0)

    def test_boundary_02_zero_denominator_safe_division(self) -> None:
        """QueryImpactRecord with zero denominator defaults safely without ZeroDivisionError."""
        record_zero = QueryImpactRecord(
            entity_id="entity_zero",
            query_frequency=0,
            failure_rate=0.0,
            mrr_drop=0.0,
            generation_id="gen_adv",
            query_denominator=0,  # Boundary: 0 denominator
        )
        impact_index = {"entity_zero": record_zero}
        score = self.planner.compute_query_impact(
            target_entities=["entity_zero"],
            query_impact_index=impact_index,
            finding_generation_id="gen_adv",
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_boundary_03_empty_validity_intervals(self) -> None:
        """Empty strings or empty tuples for validity intervals do not crash disjointness."""
        self.assertFalse(are_intervals_disjoint(("", ""), ("", "")))
        self.assertFalse(are_intervals_disjoint(("2024-01-01", ""), ("", "2024-12-31")))

    # --- CATEGORY 2: MALFORMED INPUTS ---

    def test_malformed_01_inverted_and_malformed_intervals(self) -> None:
        """Inverted dates or malformed tuples do not raise exceptions."""
        inverted_a = ("2025-12-31", "2024-01-01")
        normal_b = ("2024-06-01", "2024-12-31")
        # Inverted dates should fail safely without uncaught exception
        self.assertFalse(are_intervals_disjoint(inverted_a, normal_b))

        # Truncated or non-tuple inputs
        self.assertFalse(are_intervals_disjoint(("2024-01-01",), normal_b))  # type: ignore[arg-type]
        self.assertFalse(are_intervals_disjoint("invalid", normal_b))  # type: ignore[arg-type]

    def test_malformed_02_missing_keys_in_assertion_dicts(self) -> None:
        """Assertion dictionaries lacking required fields classify safely as UNKNOWN."""
        assertion_empty: Dict[str, Any] = {}
        assertion_valid = {
            "assertion_id": "as_valid",
            "subject_node_id": "subj_1",
            "predicate": "status",
            "object_literal": "active",
        }
        res = classify_assertion_relationship(assertion_empty, assertion_valid)
        self.assertEqual(res.outcome_type, MaintenanceOutcomeType.UNKNOWN)
        self.assertFalse(res.permits_destructive_supersession)

    # --- CATEGORY 3: CONCURRENCY & RACE CONDITIONS ---

    def test_concurrency_01_parallel_isolated_candidate_replays(self) -> None:
        """Multiple threads running candidate simulations in isolated replays do not collide."""
        def run_isolated_simulation(worker_idx: int) -> bool:
            replay = IsolatedCandidateReplay()
            try:
                for i in range(10):
                    replay.simulate_supersession(
                        superseded_id=f"as_old_{worker_idx}_{i}",
                        winner_id=f"as_new_{worker_idx}_{i}",
                        predicate="team_lead",
                    )
                report = replay.evaluate_cohorts(
                    harmed=[{"query_id": f"q_{worker_idx}", "recovered_in_candidate": True}],
                    unaffected=[{"query_id": f"q_ok_{worker_idx}", "regressed_in_candidate": False, "false_retirement": False}],
                )
                return report.is_promotion_eligible
            finally:
                replay.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(run_isolated_simulation, idx) for idx in range(8)]
            results = [f.result() for f in futures]

        self.assertTrue(all(results))
        self.assertEqual(len(results), 8)

    # --- CATEGORY 4: OVERFLOW & EXHAUSTION ---

    def test_overflow_01_recurrence_explosion_attack_bounded(self) -> None:
        """Stated recurrence count of 1,000,000 with 2 unique events is bounded strictly to 2."""
        inflated_finding = HygieneFinding(
            finding_id="find_inflated",
            category="duplicate_node",
            severity="LOW",
            target_entities=["entity_target"],
            description="Recurrence explosion attempt",
            proposed_fix="noop",
            recurrence_count=1_000_000,
            evidence_event_ids=("evt_alpha", "evt_beta", "evt_alpha", "evt_beta"),
        )
        self.assertEqual(inflated_finding.unique_recurrence_count(), 2)

        plan = self.planner.plan_maintenance([inflated_finding])
        self.assertEqual(plan.proposals[0].recurrence_count, 2)
        # Verify harm score didn't explode into millions
        self.assertLess(plan.total_harm_score, 100.0)

    def test_overflow_02_failure_rate_out_of_bounds_clamped(self) -> None:
        """Failure rate exceeding 1.0 or MRR drop exceeding 1.0 are bounded safely."""
        extreme_record = QueryImpactRecord(
            entity_id="entity_extreme",
            query_frequency=1000,
            failure_rate=5.5,  # Out of bounds
            mrr_drop=10.0,     # Out of bounds
            generation_id="gen_adv",
            query_denominator=1000,
        )
        impact_index = {"entity_extreme": extreme_record}
        score = self.planner.compute_query_impact(
            target_entities=["entity_extreme"],
            query_impact_index=impact_index,
            finding_generation_id="gen_adv",
        )
        self.assertEqual(score, 3.0)

    # --- CATEGORY 5: INVARIANT BYPASS ATTEMPTS ---

    def test_bypass_01_unauthorized_uwg_promotion_blocked(self) -> None:
        """Promotion attempt without operator approval fails closed immediately."""
        replay = IsolatedCandidateReplay()
        try:
            with self.assertRaises(PermissionError) as ctx:
                replay.promote_to_canonical(
                    approval_granted=False,
                    learning_receipt_digest="sha256:valid_digest_here",
                )
            self.assertIn("UWG_GATE_VIOLATION", str(ctx.exception))
        finally:
            replay.close()

    def test_bypass_02_missing_receipt_digest_blocked(self) -> None:
        """Promotion attempt without signed learning receipt digest fails closed."""
        replay = IsolatedCandidateReplay()
        try:
            with self.assertRaises(PermissionError) as ctx:
                replay.promote_to_canonical(
                    approval_granted=True,
                    learning_receipt_digest="",
                )
            self.assertIn("UWG_GATE_VIOLATION", str(ctx.exception))
        finally:
            replay.close()

    def test_bypass_03_multi_valued_destructive_supersession_blocked(self) -> None:
        """Classifier forbids destructive supersession on multi-valued predicates."""
        for mv_pred in ["skills", "has_role", "advisory_notes", "project_membership"]:
            self.assertTrue(is_multi_valued_predicate(mv_pred))
            res = classify_assertion_relationship(
                {"predicate": mv_pred, "object_literal": "v1"},
                {"predicate": mv_pred, "object_literal": "v2"},
            )
            self.assertFalse(res.permits_destructive_supersession)
            self.assertEqual(res.outcome_type, MaintenanceOutcomeType.COEXISTENCE)

    def test_bypass_04_cohort_regression_blocks_promotion(self) -> None:
        """Cohort evaluation with regressions or false retirements rejects promotion eligibility."""
        replay = IsolatedCandidateReplay()
        try:
            harmed = [{"query_id": "q1", "recovered_in_candidate": True}]
            unaffected_with_regression = [
                {"query_id": "q2", "regressed_in_candidate": True, "false_retirement": True}
            ]
            report = replay.evaluate_cohorts(harmed, unaffected_with_regression)
            self.assertFalse(report.is_promotion_eligible)
            self.assertEqual(report.unaffected_regressed_count, 1)
            self.assertEqual(report.false_retirements_count, 1)
        finally:
            replay.close()

    # --- CATEGORY 6: ROLLBACK & RECOVERY ---

    def test_recovery_01_isolated_rollback_restores_clean_state(self) -> None:
        """Rollback restores clean state without residual sidecar or cache keys."""
        replay = IsolatedCandidateReplay()
        try:
            replay.simulate_supersession("as_1", "as_2", "title")
            self.assertGreater(len(replay.assertion_id_mapping), 0)
            self.assertGreater(len(replay.sidecar_invalidation_keys), 0)

            replay.rollback()
            self.assertEqual(len(replay.assertion_id_mapping), 0)
            self.assertEqual(len(replay.search_doc_mapping), 0)
            self.assertEqual(len(replay.sidecar_invalidation_keys), 0)
            self.assertEqual(len(replay.cache_invalidation_keys), 0)

            # Idempotent second rollback causes no error
            replay.rollback()
            self.assertEqual(len(replay.assertion_id_mapping), 0)
        finally:
            replay.close()
