"""
Unit & fixture tests for PR Overlap Planner: pairwise merge simulation, order planning,
stacked lineage detection, and zero-mutation guarantees.

SSOT: urn:nee:pr-overlap-plan:1.0.0
"""
from __future__ import annotations

import subprocess
import tempfile
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

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pr_overlap"


class TestPROverlapPlanner(unittest.TestCase):
    """Tests pairwise simulation parity with real git merge and order planning."""

    @classmethod
    def setUpClass(cls):
        # Ensure deterministic fixture exists
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_pairwise_simulation_matches_real_git_merge_on_scratch_clone(self):
        """
        Critical Acceptance Criteria: Pairwise result matches an actual git merge
        on scratch clones for representative fixture pairs.
        """
        test_pairs = [
            ("feat/alpha-dup-a", "feat/alpha-dup-b", True),       # Identical patch -> clean
            ("feat/alpha-dup-a", "feat/beta-disjoint", True),     # Disjoint hunks -> clean
            ("feat/alpha-dup-a", "feat/gamma-overlap", False),    # Overlapping lines -> conflict
            ("feat/epsilon-blob-a", "feat/epsilon-blob-b", True), # Identical new file -> clean
        ]

        for b_a, b_b, expected_clean in test_pairs:
            # 1. Test in-memory simulation
            sim_res = simulate_pairwise_merge(
                repo_root=FIXTURE_DIR,
                base_ref="main",
                branch_a=b_a,
                branch_b=b_b,
            )
            self.assertEqual(
                sim_res.is_clean,
                expected_clean,
                f"Simulation mismatch for {b_a} vs {b_b}: got {sim_res.is_clean}, expected {expected_clean}",
            )

            # 2. Test actual real git merge on scratch clone
            with tempfile.TemporaryDirectory() as tmp_clone_dir:
                subprocess.run(
                    ["git", "clone", "--local", str(FIXTURE_DIR), tmp_clone_dir],
                    capture_output=True,
                    check=True,
                )
                clone_path = Path(tmp_clone_dir)
                subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(clone_path), capture_output=True, check=True)
                subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=str(clone_path), capture_output=True, check=True)

                # Checkout branch_a
                subprocess.run(["git", "checkout", b_a], cwd=str(clone_path), capture_output=True, check=True)

                # Execute real git merge with remote tracking ref
                real_merge = subprocess.run(
                    ["git", "merge", "--no-commit", "--no-ff", f"origin/{b_b}"],
                    cwd=str(clone_path),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                real_clean = real_merge.returncode == 0

                self.assertEqual(
                    sim_res.is_clean,
                    real_clean,
                    f"Real git merge parity mismatch for {b_a} vs {b_b}: sim={sim_res.is_clean}, real={real_clean}",
                )

    def test_planner_conflict_count_le_alphabetical_baseline(self):
        """
        Acceptance Criteria: Planner order has <= conflicts than any naive
        alphabetical order on all fixtures.
        """
        plan = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["feat/alpha-dup-a", "feat/beta-disjoint", "feat/gamma-overlap"],
        )
        self.assertLessEqual(plan.total_conflicts, plan.alphabetical_conflicts)
        self.assertTrue(plan.zero_mutation_verified)

    def test_stacked_lineage_and_onto_command(self):
        """
        Acceptance Criteria: Stacked-lineage fixture yields --onto plan.
        """
        is_stacked = detect_stacked_lineage(
            FIXTURE_DIR,
            "feat/beta-disjoint",
            "feat/theta-stacked-on-beta",
        )
        self.assertTrue(is_stacked, "feat/theta-stacked-on-beta should be detected as stacked on beta-disjoint")

        rec = build_rebase_recommendation(
            repo_root=FIXTURE_DIR,
            branch="feat/theta-stacked-on-beta",
            upstream_branch="feat/beta-disjoint",
            target_base="main",
        )
        self.assertTrue(rec.is_stacked)
        self.assertEqual(
            rec.rebase_command,
            "git rebase --onto main feat/beta-disjoint feat/theta-stacked-on-beta",
        )

    def test_zero_mutation_guarantee(self):
        """
        Acceptance Criteria: No ref/worktree mutation (verified by before/after
        git for-each-ref + git status digests).
        """
        digest_before = compute_repo_state_digest(FIXTURE_DIR)
        plan = plan_merge_order(
            repo_root=FIXTURE_DIR,
            base_ref="main",
            branches=["feat/alpha-dup-a", "feat/beta-disjoint"],
        )
        digest_after = compute_repo_state_digest(FIXTURE_DIR)

        self.assertEqual(digest_before, digest_after)
        self.assertTrue(plan.zero_mutation_verified)
        self.assertEqual(plan.schema_version, PLAN_SCHEMA_VERSION)

    def test_decision_d3_search_strategy_selection(self):
        """
        Tests that Decision D3 engages exhaustive search when N <= 8,
        and greedy lookahead when N > 8.
        """
        small_set = ["feat/alpha-dup-a", "feat/beta-disjoint", "feat/gamma-overlap"]
        plan_small = plan_merge_order(repo_root=FIXTURE_DIR, base_ref="main", branches=small_set)
        self.assertEqual(plan_small.search_strategy, "exhaustive_pruned")

        large_set = [f"branch-{i}" for i in range(9)]
        plan_large = plan_merge_order(repo_root=FIXTURE_DIR, base_ref="main", branches=large_set)
        self.assertEqual(plan_large.search_strategy, "greedy_lookahead")


if __name__ == "__main__":
    unittest.main()
