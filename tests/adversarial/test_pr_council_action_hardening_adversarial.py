"""tests/adversarial/test_pr_council_action_hardening_adversarial.py

Adversarial and boundary test suite for 5-Agent Council action item hardening,
complexity assessment resilience, and degenerate queue deliberation.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from tools.pr_governance_core.complexity import evaluate_refactoring_complexity
from tools.pr_governance_core.council import (
    deliberate_council_queue,
    harden_action_item,
)


class TestPRCouncilActionHardeningAdversarial(unittest.TestCase):
    def test_boundary_payload_missing_keys_safe_evaluation(self):
        """Verify that malformed or stripped branch dicts evaluate without throwing KeyError/TypeError."""
        stripped_item: Dict[str, Any] = {"branch": "feat/stripped"}
        comp = evaluate_refactoring_complexity(stripped_item)
        self.assertIn(comp.size, {"XS", "S", "M", "L", "XL"})
        self.assertTrue(0 <= comp.rank <= 4)
        self.assertGreaterEqual(comp.score, 0)

        # Deliberate on stripped items
        deliberation = deliberate_council_queue([stripped_item, {"branch": "feat/other"}])
        self.assertEqual(len(deliberation.assessments), 2)
        self.assertEqual(len(deliberation.debate_rationales), 1)

    def test_boundary_payload_massive_churn_and_negative_churn(self):
        """Verify resilience against extreme integers (massive or negative churn)."""
        massive_item = {
            "branch": "feat/massive-overhaul",
            "churn": 2_500_000,
            "file_count": 10_000,
            "behind": 5_000,
            "clean_merge": False,
            "changed_files": ["storage/database.py", "dual_pipeline/engine.py"],
            "open_waves": "Wave 1, Wave 2, Wave 3",
        }
        comp_massive = evaluate_refactoring_complexity(massive_item)
        self.assertEqual(comp_massive.size, "XL")
        self.assertEqual(comp_massive.rank, 4)

        negative_item = {
            "branch": "feat/negative-glitch",
            "churn": -5000,
            "file_count": -10,
            "behind": -5,
        }
        comp_negative = evaluate_refactoring_complexity(negative_item)
        self.assertEqual(comp_negative.size, "XS")
        self.assertEqual(comp_negative.rank, 0)

    def test_boundary_empty_and_single_item_council(self):
        """Verify 0-branch and 1-branch council queues handle boundary gracefully without IndexError."""
        empty_delib = deliberate_council_queue([])
        self.assertEqual(len(empty_delib.assessments), 0)
        self.assertEqual(len(empty_delib.debate_rationales), 0)

        single_item = {"branch": "feat/lone-branch", "tier": "P0", "clean_merge": True}
        single_delib = deliberate_council_queue([single_item])
        self.assertEqual(len(single_delib.assessments), 1)
        self.assertEqual(len(single_delib.debate_rationales), 0)

    def test_adversarial_vague_action_rejection_matrix(self):
        """Verify that every prohibited vague/placeholder action is rejected and hardened."""
        prohibited_actions = [
            "Review and proceed",
            "Work in progress",
            "WIP",
            "Needs fix",
            "Fix tests",
            "Check code",
            "Follow up",
            "TBD",
            "Investigate",
            "[object Object]",
            "undefined",
            "",
            "   ",
            "...",
        ]

        test_item = {
            "branch": "feat/vague-victim",
            "tier": "P0",
            "clean_merge": True,
            "ahead": 2,
            "behind": 0,
            "dirty": False,
            "open_waves": "—",
        }

        for vague in prohibited_actions:
            hardened = harden_action_item(test_item, vague, {})
            # Must NOT equal the vague input
            self.assertNotEqual(hardened.strip(), vague.strip())
            # Must contain concrete execution guidance
            self.assertTrue(
                "tools/pr_governance.py" in hardened or "merge" in hardened or "rebase" in hardened or "pytest" in hardened,
                f"Hardened action for '{vague}' lacked concrete tool or guidance: '{hardened}'",
            )

    def test_adversarial_dirty_authoring_inception_action_hardening(self):
        """Verify authoring inception branches (0 ahead, dirty files) harden into commit retention."""
        inception_item = {
            "branch": "feat/new-draft",
            "tier": "P2",
            "ahead": 0,
            "behind": 10,
            "dirty": True,
            "clean_merge": True,
            "worktree_path": ".worktrees/new_draft",
            "open_waves": "Wave 1",
        }
        hardened = harden_action_item(inception_item, "Review and proceed", {})
        self.assertIn("Commit", hardened)
        self.assertIn("before rebasing onto main", hardened)

    def test_adversarial_diverged_conflict_action_hardening(self):
        """Verify conflicting diverged branch actions harden with rebase and conflict commands."""
        conflict_item = {
            "branch": "feat/conflict-storm",
            "tier": "P1",
            "ahead": 4,
            "behind": 25,
            "clean_merge": False,
            "manual_conflicts": 3,
            "rebase_effort": "HIGH",
        }
        hardened = harden_action_item(conflict_item, "WIP", {})
        self.assertIn("Rebase onto main", hardened)
        self.assertIn("effort: HIGH", hardened)


if __name__ == "__main__":
    unittest.main()
