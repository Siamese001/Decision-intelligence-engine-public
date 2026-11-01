"""
Adversarial and boundary test suite for Cherry-Pick Assessor (Wave 5 / Unified Wave 8).
Validates behavior on nonexistent branches, zero commits, malicious subjects, and zero mutation.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.pr_overlap_core import (
    assess_branch_cherry_pick,
    compute_repo_state_digest,
    verify_scratch_agreement,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pr_overlap"


class TestPRCherryPickAdversarial(unittest.TestCase):
    """Adversarial and edge-case testing for cherry-pick assessor."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_nonexistent_branch_fails_gracefully(self):
        """Validates that a nonexistent branch ref returns empty plan without crashing."""
        digest_before = compute_repo_state_digest(FIXTURE_DIR)
        plan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/nonexistent-ghost-branch",
            base_ref="main",
            verify_scratch=True,
        )
        digest_after = compute_repo_state_digest(FIXTURE_DIR)

        self.assertEqual(plan.total_commits, 0)
        self.assertEqual(len(plan.extract_set), 0)
        self.assertEqual(digest_before, digest_after)
        self.assertTrue(plan.zero_mutation_verified)

    def test_branch_identical_to_base_zero_commits(self):
        """Validates that a branch pointing directly to base_ref yields 0 commits and clean exit."""
        plan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="main",
            base_ref="main",
            verify_scratch=True,
        )
        self.assertEqual(plan.total_commits, 0)
        self.assertEqual(len(plan.extract_set), 0)
        self.assertTrue(plan.zero_mutation_verified)

    def test_nonexistent_base_ref(self):
        """Validates that a nonexistent base ref does not cause unhandled crash."""
        plan = assess_branch_cherry_pick(
            repo_root=FIXTURE_DIR,
            branch="feat/cherry-clean",
            base_ref="refs/heads/ghost_base_branch",
            verify_scratch=False,
        )
        self.assertEqual(plan.total_commits, 0)
        self.assertTrue(plan.zero_mutation_verified)

    def test_empty_extract_set_scratch_verification(self):
        """Validates that verify_scratch_agreement returns True for empty set."""
        self.assertTrue(verify_scratch_agreement(FIXTURE_DIR, "main", []))

    def test_zero_mutation_under_adversarial_invocations(self):
        """Acceptance Criteria: Zero repository mutation across repeated adversarial runs."""
        digest_before = compute_repo_state_digest(FIXTURE_DIR)

        for hostile_ref in ["!@#$%^&*()", "head~999", "non_existent", ""]:
            assess_branch_cherry_pick(
                repo_root=FIXTURE_DIR,
                branch=hostile_ref,
                base_ref="main",
                verify_scratch=False,
            )

        digest_after = compute_repo_state_digest(FIXTURE_DIR)
        self.assertEqual(digest_before, digest_after)


if __name__ == "__main__":
    unittest.main()
