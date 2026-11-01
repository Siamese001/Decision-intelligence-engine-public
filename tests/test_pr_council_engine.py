"""tests/test_pr_council_engine.py

Unit tests for the 5-Agent Council deliberation and action item hardening engine.
Verifies role coverage, adversarial action hardening, and queue merge sequence rationalization.
"""
from __future__ import annotations

import unittest
from tools.pr_governance_core.council import (
    CouncilRole,
    deliberate_branch,
    deliberate_council_queue,
    harden_action_item,
)


class TestPRCouncilEngine(unittest.TestCase):
    def test_deliberate_branch_audits_all_six_roles(self):
        item = {
            "branch": "feat/clean-branch",
            "ahead": 2,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P1",
            "churn": 80,
            "file_count": 2,
            "changed_files": ["tools/helper.py", "tests/test_helper.py"],
        }
        res = deliberate_branch(item)
        self.assertEqual(res.branch, "feat/clean-branch")
        self.assertEqual(len(res.findings), 6)
        for role in CouncilRole:
            self.assertIn(role.value, res.findings)
        self.assertIn("Execute 30-gate PR review", res.hardened_action)

    def test_action_item_hardening_replaces_vague_placeholders(self):
        item = {
            "branch": "feat/unready-work",
            "ahead": 0,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P2",
        }
        vague_inputs = [
            "Review and proceed",
            "review and proceed",
            "Fix tests",
            "Work in progress",
            "Triage",
        ]
        for vague in vague_inputs:
            hardened = harden_action_item(item, vague, {})
            self.assertNotIn("Review and proceed", hardened)
            self.assertNotIn("Work in progress", hardened)
            self.assertTrue(len(hardened) > 10)

    def test_action_item_hardening_for_inception_work(self):
        item = {
            "branch": "feat/inception-analysis",
            "ahead": 0,
            "behind": 4,
            "clean_merge": True,
            "dirty": True,
            "dirty_files_count": 3,
            "tier": "P0",
        }
        res = deliberate_branch(item)
        self.assertEqual(res.disposition, "Complete In-Flight Work")
        self.assertIn("Commit 3 authored file(s)", res.hardened_action)
        self.assertIn("before rebasing onto main", res.hardened_action)

    def test_action_item_hardening_for_conflicts(self):
        item = {
            "branch": "feat/conflicting-feature",
            "ahead": 5,
            "behind": 10,
            "clean_merge": False,
            "dirty": False,
            "tier": "P1",
            "rebase_effort": "HIGH",
        }
        res = deliberate_branch(item)
        self.assertEqual(res.disposition, "Promote to Main")
        self.assertIn("effort: HIGH", res.hardened_action)
        self.assertIn("resolve conflicting files", res.hardened_action)

    def test_action_item_hardening_for_open_waves(self):
        item = {
            "branch": "feat/multi-wave-plan",
            "ahead": 3,
            "behind": 2,
            "clean_merge": True,
            "dirty": False,
            "tier": "P1",
            "open_waves": "Wave 2, Wave 3",
        }
        res = deliberate_branch(item)
        self.assertEqual(res.disposition, "Complete In-Flight Work")
        self.assertIn("Complete open wave(s) [Wave 2, Wave 3]", res.hardened_action)
        self.assertIn("Class B pytest", res.hardened_action)

    def test_deliberate_council_queue_sequence(self):
        queue = [
            {
                "branch": "feat/quick-ready",
                "ahead": 1,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "tier": "P0",
                "churn": 40,
                "file_count": 1,
            },
            {
                "branch": "feat/diverged-heavy",
                "ahead": 4,
                "behind": 15,
                "clean_merge": False,
                "dirty": False,
                "tier": "P1",
                "churn": 1200,
                "file_count": 14,
            },
        ]
        result = deliberate_council_queue(queue)
        self.assertEqual(len(result.assessments), 2)
        self.assertEqual(result.merge_sequence, ["feat/quick-ready", "feat/diverged-heavy"])
        self.assertEqual(len(result.debate_rationales), 1)
        self.assertIn("Merge #1 `feat/quick-ready`", result.debate_rationales[0])
        self.assertIn("before #2 `feat/diverged-heavy`", result.debate_rationales[0])

        d = result.to_dict()
        self.assertIn("timestamp", d)
        self.assertIn("assessments", d)
        self.assertIn("merge_sequence", d)

    def test_action_item_hardening_for_stale_unstarted_plan(self):
        item = {
            "branch": "feat/efficiency-gap-council-plan",
            "ahead": 1,
            "behind": 543,
            "clean_merge": True,
            "dirty": False,
            "tier": "P2",
            "changed_files": ["docs/plans/WAVE_PLAN_EFFICIENCY_GAP_REMEDIATION.md"],
            "diff_category": "DOCS_ONLY_UNSTARTED",
            "is_docs_only_plan": True,
        }
        res = deliberate_branch(item)
        self.assertEqual(res.disposition, "Decommission (No Value)")
        self.assertIn("Safely prune stale unstarted planning worktree", res.hardened_action)
        self.assertIn("543 commits behind", res.hardened_action)
        diff_finding = res.findings[CouncilRole.DIFF_INTELLIGENCE.value][0]
        self.assertEqual(diff_finding.verdict, "OBJECT")


if __name__ == "__main__":
    unittest.main()
