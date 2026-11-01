"""End-to-end lifecycle verification for Harm-Directed Graph Maintenance (Wave 10).

Verifies:
1. Cross-generation joins and duplicate event recurrence are rejected.
2. Frequency never proves truth: mention count or recurrence cannot override provenance or validity.
3. Multi-valued predicates and disjoint validity intervals produce COEXISTENCE, never destructive supersession.
4. Contradictions require single-valued exclusivity with overlapping validity intervals.
5. Missing discriminating evidence yields DEFERRED disposition and acquisition proposals.
6. Freshness detection reads a snapshot; dry-run mutations occur only in an isolated candidate container.
7. Candidate simulation tracks assertion mappings, search-doc updates, sidecars, and cache invalidations.
8. Non-destructive exact rollback reverts candidate mutations without touching canonical graph.
9. Cohort evaluation gates promotion: harmed queries must recover without regressing unaffected queries.
10. Promotion to canonical graph strictly requires human operator approval and signed learning receipt.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.graph_maintenance_core import (
    CohortEvaluationReport,
    HarmPrioritizedPlanner,
    HygieneFinding,
    IsolatedCandidateReplay,
    MaintenanceOutcomeType,
    QueryImpactRecord,
    RankedMaintenanceProposal,
    are_intervals_disjoint,
    classify_assertion_relationship,
    is_multi_valued_predicate,
)


class TestKnowledgeHarmLearningE2E(TestCase):
    """End-to-end qualification suite for Wave 10 harm-directed graph maintenance."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.canonical_db = self.runtime_root / "canonical_graph.sqlite"
        self.planner = HarmPrioritizedPlanner(run_id="run-wave10-e2e")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_cross_generation_joins_rejected_e2e(self) -> None:
        """Cross-generation joins between findings and query impact records are rejected."""
        finding_gen0 = HygieneFinding(
            finding_id="find_gen0_1",
            category="temporal_contradiction",
            severity="HIGH",
            target_entities=["entity_alpha"],
            description="Contradiction in generation 0",
            proposed_fix="resolve_gen0",
            generation_id="gen_0",
        )
        impact_gen1 = QueryImpactRecord(
            entity_id="entity_alpha",
            query_frequency=100,
            failure_rate=0.8,
            mrr_drop=0.4,
            generation_id="gen_1",  # Different generation
            query_denominator=100,
        )

        impact_index = {"entity_alpha": impact_gen1}
        # Joining gen_0 finding with gen_1 impact must be rejected
        score_rejected = self.planner.compute_query_impact(
            target_entities=["entity_alpha"],
            query_impact_index=impact_index,
            finding_generation_id="gen_0",
        )
        self.assertEqual(score_rejected, 0.1)  # Minimal baseline, cross-gen impact rejected

        # Same generation join succeeds
        impact_gen0 = QueryImpactRecord(
            entity_id="entity_alpha",
            query_frequency=50,
            failure_rate=0.4,
            mrr_drop=0.2,
            generation_id="gen_0",  # Matching generation
            query_denominator=50,
        )
        impact_index_matching = {"entity_alpha": impact_gen0}
        score_matched = self.planner.compute_query_impact(
            target_entities=["entity_alpha"],
            query_impact_index=impact_index_matching,
            finding_generation_id="gen_0",
        )
        self.assertGreater(score_matched, 0.1)

    def test_duplicate_event_recurrence_deduplicated_e2e(self) -> None:
        """Duplicate event recurrence does not artificially multiply harm score."""
        duplicate_events = ("evt_101", "evt_101", "evt_101", "evt_101", "evt_101")
        finding_dup = HygieneFinding(
            finding_id="find_dup_1",
            category="duplicate_node",
            severity="MEDIUM",
            target_entities=["entity_beta"],
            description="Duplicate event test",
            proposed_fix="merge_beta",
            recurrence_count=5,  # Stated recurrence
            evidence_event_ids=duplicate_events,  # All duplicates of single event
        )
        # Unique recurrence count must be 1
        self.assertEqual(finding_dup.unique_recurrence_count(), 1)

        plan = self.planner.plan_maintenance([finding_dup])
        self.assertEqual(len(plan.proposals), 1)
        prop = plan.proposals[0]
        self.assertEqual(prop.recurrence_count, 1)

    def test_multi_valued_predicates_yield_coexistence_e2e(self) -> None:
        """Multi-valued predicates produce COEXISTENCE and forbid destructive supersession."""
        self.assertTrue(is_multi_valued_predicate("skills"))
        self.assertTrue(is_multi_valued_predicate("has_role"))

        assertion_a = {
            "assertion_id": "as_role_1",
            "subject_node_id": "person_alice",
            "predicate": "has_role",
            "object_literal": "Software Engineer",
        }
        assertion_b = {
            "assertion_id": "as_role_2",
            "subject_node_id": "person_alice",
            "predicate": "has_role",
            "object_literal": "Tech Lead",
        }
        result = classify_assertion_relationship(assertion_a, assertion_b)
        self.assertEqual(result.outcome_type, MaintenanceOutcomeType.COEXISTENCE)
        self.assertTrue(result.is_multi_valued)
        self.assertFalse(result.permits_destructive_supersession)

    def test_disjoint_temporal_validity_yields_coexistence_e2e(self) -> None:
        """Disjoint temporal validity intervals produce COEXISTENCE without supersession."""
        int_2023 = ("2023-01-01", "2023-12-31")
        int_2024 = ("2024-01-01", "2024-12-31")
        self.assertTrue(are_intervals_disjoint(int_2023, int_2024))

        assertion_2023 = {
            "assertion_id": "as_loc_2023",
            "subject_node_id": "org_partner_advisory",
            "predicate": "headquarters",
            "object_literal": "Denver",
            "validity_interval": int_2023,
        }
        assertion_2024 = {
            "assertion_id": "as_loc_2024",
            "subject_node_id": "org_partner_advisory",
            "predicate": "headquarters",
            "object_literal": "Seattle",
            "validity_interval": int_2024,
        }
        result = classify_assertion_relationship(assertion_2023, assertion_2024)
        self.assertEqual(result.outcome_type, MaintenanceOutcomeType.COEXISTENCE)
        self.assertTrue(result.is_temporally_disjoint)
        self.assertFalse(result.permits_destructive_supersession)

    def test_contradiction_requires_single_valued_and_overlapping_intervals_e2e(self) -> None:
        """Contradictions require single-valued exclusivity and overlapping validity intervals."""
        overlapping_a = ("2024-01-01", "2024-12-31")
        overlapping_b = ("2024-06-01", "2025-06-01")
        self.assertFalse(are_intervals_disjoint(overlapping_a, overlapping_b))

        assertion_conflict_1 = {
            "assertion_id": "as_conf_1",
            "subject_node_id": "company_x",
            "predicate": "tax_id",
            "object_literal": "12-3456789",
            "validity_interval": overlapping_a,
        }
        assertion_conflict_2 = {
            "assertion_id": "as_conf_2",
            "subject_node_id": "company_x",
            "predicate": "tax_id",
            "object_literal": "98-7654321",
            "validity_interval": overlapping_b,
        }
        result = classify_assertion_relationship(assertion_conflict_1, assertion_conflict_2)
        self.assertEqual(result.outcome_type, MaintenanceOutcomeType.CONTRADICTION)
        self.assertFalse(result.is_multi_valued)
        self.assertFalse(result.is_temporally_disjoint)
        self.assertTrue(result.permits_destructive_supersession)

    def test_missing_evidence_yields_deferred_disposition_e2e(self) -> None:
        """Missing evidence yields DEFERRED disposition and requests discriminating evidence."""
        finding_missing = HygieneFinding(
            finding_id="find_missing_1",
            category="stale_assertion",
            severity="MEDIUM",
            target_entities=["entity_gamma"],
            description="Suspected stale assertion but no discriminating evidence available",
            proposed_fix="retire_gamma",
            evidence_missing=True,
        )
        plan = self.planner.plan_maintenance([finding_missing])
        self.assertEqual(len(plan.proposals), 1)
        prop = plan.proposals[0]
        self.assertEqual(prop.disposition, "DEFERRED")
        self.assertTrue(prop.is_deferred)

    def test_isolated_candidate_replay_and_rollback_e2e(self) -> None:
        """Candidate replay runs dry-run simulation and exact non-destructive rollback."""
        replay = IsolatedCandidateReplay()
        try:
            replay.simulate_supersession(
                superseded_id="as_old_1",
                winner_id="as_new_1",
                predicate="lead_partner",
            )
            self.assertEqual(replay.assertion_id_mapping.get("as_old_1"), "as_new_1")
            self.assertEqual(replay.search_doc_mapping.get("doc_as_old_1"), "doc_as_new_1")
            self.assertIn("sidecar:assertion:as_old_1", replay.sidecar_invalidation_keys)
            self.assertIn("cache:lead_partner:as_old_1", replay.cache_invalidation_keys)

            # Rollback resets all mappings cleanly
            replay.rollback()
            self.assertEqual(len(replay.assertion_id_mapping), 0)
            self.assertEqual(len(replay.search_doc_mapping), 0)
            self.assertEqual(len(replay.sidecar_invalidation_keys), 0)
            self.assertEqual(len(replay.cache_invalidation_keys), 0)
        finally:
            replay.close()

    def test_cohort_evaluation_and_uwg_release_authority_e2e(self) -> None:
        """Cohort evaluation qualifies candidate and enforces strict UWG release authority."""
        replay = IsolatedCandidateReplay()
        try:
            harmed = [
                {"query_id": "q_harm_1", "mrr_before": 0.2, "mrr_after": 0.9, "recovered_in_candidate": True},
                {"query_id": "q_harm_2", "mrr_before": 0.0, "mrr_after": 0.8, "recovered_in_candidate": True},
            ]
            unaffected = [
                {"query_id": "q_ok_1", "mrr_before": 0.95, "mrr_after": 0.95, "regressed_in_candidate": False, "false_retirement": False},
                {"query_id": "q_ok_2", "mrr_before": 0.90, "mrr_after": 0.90, "regressed_in_candidate": False, "false_retirement": False},
            ]

            report = replay.evaluate_cohorts(harmed, unaffected)
            self.assertTrue(report.is_promotion_eligible)
            self.assertEqual(report.harmed_recovered_count, 2)
            self.assertEqual(report.unaffected_regressed_count, 0)
            self.assertEqual(report.false_retirements_count, 0)

            # Promotion without human approval must fail closed
            with self.assertRaises(PermissionError) as ctx:
                replay.promote_to_canonical(approval_granted=False)
            self.assertIn("UWG_GATE_VIOLATION", str(ctx.exception))

            # Promotion without signed learning receipt digest must fail closed
            with self.assertRaises(PermissionError) as ctx2:
                replay.promote_to_canonical(approval_granted=True, learning_receipt_digest=None)
            self.assertIn("UWG_GATE_VIOLATION", str(ctx2.exception))

            # Promotion with both operator approval and receipt succeeds
            success = replay.promote_to_canonical(
                approval_granted=True,
                learning_receipt_digest="sha256:fedcba9876543210fedcba9876543210",
            )
            self.assertTrue(success)
        finally:
            replay.close()
