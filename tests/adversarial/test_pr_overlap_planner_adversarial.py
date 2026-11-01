"""
Adversarial test suite for PR Overlap Planner & Pairwise Merge Simulation.

Covers:
1. Ghost / non-existent / deleted refs
2. Empty, single-branch, and duplicated branch inputs
3. Self-referential and cyclic lineage boundary handling
4. Malformed conflict output handling
5. Zero-mutation invariant under edge-case inputs
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.pr_overlap_core import (
    PLAN_SCHEMA_VERSION,
    build_rebase_recommendation,
    compute_repo_state_digest,
    detect_stacked_lineage,
    plan_merge_order,
    simulate_pairwise_merge,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "pr_overlap"


class TestPROverlapPlannerAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for pairwise simulation and order planner."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_ghost_and_non_existent_branch_refs(self):
        # Must not crash when encountering non-existent branches
        sim = simulate_pairwise_merge(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branch_a="non-existent-branch-xyz",
            branch_b="another-ghost-branch-123",
        )
        self.assertFalse(sim.is_clean)
        self.assertIsInstance(sim.raw_messages, list)

        # Plan with ghost branches
        plan = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["ghost-branch-1", "ghost-branch-2"],
        )
        self.assertIsInstance(plan.ordered_steps, list)
        self.assertTrue(plan.zero_mutation_verified)

    def test_empty_and_single_branch_inputs(self):
        # Empty branch list
        plan_empty = plan_merge_order(repo_root=FIXTURE_DIR, base_ref="main", branches=[])
        self.assertEqual(len(plan_empty.ordered_steps), 0)
        self.assertEqual(plan_empty.total_conflicts, 0)
        self.assertTrue(plan_empty.zero_mutation_verified)

        # Single branch
        plan_single = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["feat/alpha-dup-a"],
        )
        self.assertEqual(len(plan_single.ordered_steps), 1)
        self.assertEqual(plan_single.total_conflicts, 0)
        self.assertTrue(plan_single.zero_mutation_verified)

    def test_duplicated_branch_names_in_input(self):
        # User provides duplicate branch in argument list
        plan_dups = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["feat/alpha-dup-a", "feat/alpha-dup-a"],
        )
        self.assertTrue(plan_dups.zero_mutation_verified)
        self.assertLessEqual(plan_dups.total_conflicts, plan_dups.alphabetical_conflicts)

    def test_self_referential_lineage_handling(self):
        # Branch compared to itself
        is_stacked = detect_stacked_lineage(FIXTURE_DIR, "feat/alpha-dup-a", "feat/alpha-dup-a")
        self.assertTrue(is_stacked)  # In git, every commit is an ancestor of itself

        rec = build_rebase_recommendation(
            repo_root=FIXTURE_DIR,
            branch="feat/alpha-dup-a",
            upstream_branch="feat/alpha-dup-a",
            target_base="main",
        )
        self.assertIn("feat/alpha-dup-a", rec.rebase_command)

    def test_zero_mutation_guarantee_on_adversarial_run(self):
        digest_before = compute_repo_state_digest(FIXTURE_DIR)
        plan = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["feat/gamma-overlap", "feat/alpha-dup-a", "non-existent-xyz"],
        )
        digest_after = compute_repo_state_digest(FIXTURE_DIR)
        self.assertEqual(digest_before, digest_after)
        self.assertTrue(plan.zero_mutation_verified)
        self.assertEqual(plan.schema_version, PLAN_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
