"""
Unit tests for Cherry-Pick Assessor (Wave 5 / Unified Wave 8).
Validates per-commit classification into all 6 SSOT classes (§5.5),
extract set synthesis, 100% scratch clone agreement, and zero mutation.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_overlap_core import (
    BranchCherryPickPlan,
    CherryPickClass,
    assess_branch_cherry_pick,
    compute_repo_state_digest,
    verify_scratch_agreement,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "pr_overlap"


class TestPRCherryPickAssessor(unittest.TestCase):
    """Tests commit classification, extract sets, and scratch clone parity."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_pick_clean_class_and_extract_set(self):
        """Validates PICK_CLEAN verdict and automatic inclusion in extract set."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-clean",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        self.assertEqual(plan.verdicts[0].verdict, CherryPickClass.PICK_CLEAN)
        self.assertTrue(plan.verdicts[0].is_extractable)
        self.assertEqual(len(plan.extract_set), 1)
        self.assertTrue(plan.scratch_agreement_verified)
        self.assertTrue(plan.zero_mutation_verified)

    def test_pick_needs_prereq_dependency_chain(self):
        """Validates PICK_NEEDS_PREREQ with prerequisite graph and satisfied extraction."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-prereq",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 2)
        v1, v2 = plan.verdicts[0], plan.verdicts[1]

        self.assertEqual(v1.verdict, CherryPickClass.PICK_CLEAN)
        self.assertEqual(v2.verdict, CherryPickClass.PICK_NEEDS_PREREQ)
        self.assertIn(v1.commit_sha, v2.prerequisites)
        self.assertEqual(len(plan.extract_set), 2)
        self.assertTrue(plan.scratch_agreement_verified)
        self.assertTrue(plan.zero_mutation_verified)

    def test_pick_conflicts_class(self):
        """Validates PICK_CONFLICTS detection against diverged base_ref."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-conflict",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        v = plan.verdicts[0]
        self.assertEqual(v.verdict, CherryPickClass.PICK_CONFLICTS)
        self.assertIn("conflict_target.py", v.conflict_files)
        self.assertEqual(v.effort_band, "LOW")
        self.assertEqual(len(plan.extract_set), 0)
        self.assertTrue(plan.zero_mutation_verified)

    def test_already_in_main_class(self):
        """Validates ALREADY_IN_MAIN detection via patch-id equality with main."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-already-in-main",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        v = plan.verdicts[0]
        self.assertEqual(v.verdict, CherryPickClass.ALREADY_IN_MAIN)
        self.assertIn("already landed", v.rationale)
        self.assertEqual(len(plan.extract_set), 0)
        self.assertTrue(plan.zero_mutation_verified)

    def test_duplicate_in_sibling_class(self):
        """Validates DUPLICATE_IN_SIBLING detection across sibling branches."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/alpha-dup-b",
            base_ref="main",
            sibling_branches=["feat/alpha-dup-a", "feat/beta-disjoint"],
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        v = plan.verdicts[0]
        self.assertEqual(v.verdict, CherryPickClass.DUPLICATE_IN_SIBLING)
        self.assertIn("feat/alpha-dup-a", [m["branch"] for m in v.sibling_matches])
        self.assertTrue(plan.zero_mutation_verified)

    def test_split_candidate_class(self):
        """Validates SPLIT_CANDIDATE classification for mixed-concern commit."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-split-candidate",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        v = plan.verdicts[0]
        self.assertEqual(v.verdict, CherryPickClass.SPLIT_CANDIDATE)
        self.assertIn("distinct architectural domains", v.rationale)
        self.assertTrue(plan.zero_mutation_verified)

    def test_empty_commit_handling(self):
        """Validates graceful handling of empty commits without crashing."""
        plan: BranchCherryPickPlan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-empty-commit",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 1)
        self.assertEqual(plan.verdicts[0].verdict, CherryPickClass.EMPTY_COMMIT)
        self.assertEqual(len(plan.extract_set), 0)
        self.assertTrue(plan.zero_mutation_verified)

    def test_scratch_clone_agreement_real_git_cherry_pick(self):
        """
        Acceptance Criteria: 100% agreement between in-memory simulation
        and real git cherry-pick execution on scratch clone.
        """
        plan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-prereq",
            base_ref="main",
            verify_scratch=False,
        )
        self.assertTrue(len(plan.extract_set) > 0)

        # Execute isolated real git cherry-pick on scratch clone
        with tempfile.TemporaryDirectory() as tmp_dir:
            scratch = Path(tmp_dir) / "test_scratch"
            subprocess.run(["git", "clone", "--local", str(FIXTURE_DIR), str(scratch)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(scratch), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tester@local"], cwd=str(scratch), check=True, capture_output=True)
            subprocess.run(["git", "checkout", "main"], cwd=str(scratch), check=True, capture_output=True)

            res = subprocess.run(["git", "cherry-pick", *plan.extract_set], cwd=str(scratch), capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Scratch cherry-pick failed: {res.stderr}")

        # Also verify via helper
        verified = verify_scratch_agreement(FIXTURE_DIR, "main", plan.extract_set)
        self.assertTrue(verified)

    def test_zero_mutation_invariant(self):
        """Acceptance Criteria: Zero repository or working tree mutation."""
        digest_before = compute_repo_state_digest(FIXTURE_DIR)
        assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-clean",
            base_ref="main",
            verify_scratch=True,
        )
        digest_after = compute_repo_state_digest(FIXTURE_DIR)
        self.assertEqual(digest_before, digest_after)


if __name__ == "__main__":
    unittest.main()
