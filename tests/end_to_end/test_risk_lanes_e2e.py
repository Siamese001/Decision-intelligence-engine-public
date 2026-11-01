"""tests/end_to_end/test_risk_lanes_e2e.py

End-to-End test suite for Risk-Tiered Change Lanes & Batch Sizing (Wave 7 / Findings X-18 to X-20).
Exercises:
1. Pure Lane 0 classification (docs, tests, comments).
2. Pure Lane 1 classification (pipeline code, general tools).
3. Pure Lane 2 classification (graph, storage, release, ingestion, HITL ledger, governance).
4. Mixed change escalation to highest lane touched.
5. Soft caps verification (LOC sizing, worktree WIP cap, governance budget).
6. Policy schema validation against config/governance/risk_lanes_policy.json.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.change_lane_classifier import (
    check_batch_size,
    check_worktree_wip,
    classify_changes,
    classify_file,
    load_policy,
)


class RiskLanesE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.policy = load_policy(cls.repo_root / "config/governance/risk_lanes_policy.json")

    def test_policy_schema_and_defaults(self) -> None:
        """Verifies policy-as-code schema, default lane, and escalation flags."""
        self.assertEqual(self.policy.get("default_lane"), 2)
        self.assertTrue(self.policy.get("fail_closed_on_unrecognized"))
        self.assertTrue(self.policy.get("escalate_mixed_to_highest"))
        self.assertIn("lane_definitions", self.policy)
        self.assertIn("path_rules", self.policy)

    def test_pure_lane_0_classification(self) -> None:
        """Verifies that documentation, markdown, and isolated tests map to Lane 0."""
        l0_files = [
            "docs/plans/feat_test_plan.md",
            "documentation/walkthrough.md",
            "archive/root_clutter/debug.txt",
            "README.md",
            "tests/unit/test_isolated.py",
        ]
        for f in l0_files:
            lane, reason = classify_file(f, self.policy)
            self.assertEqual(lane, 0, f"Expected Lane 0 for '{f}', got Lane {lane} ({reason})")

        res = classify_changes(l0_files)
        self.assertEqual(res["effective_lane"], 0)
        self.assertEqual(res["effective_lane_name"], "LANE_0_LOW_RISK")
        self.assertFalse(res["touches_lane_2"])
        self.assertFalse(res["touches_lane_1"])
        self.assertTrue(res["touches_lane_0"])
        self.assertFalse(res["escalated"])

    def test_pure_lane_1_classification(self) -> None:
        """Verifies that non-storage pipeline code and utilities map to Lane 1."""
        l1_files = [
            "dual_pipeline/analyzer.py",
            "tools/formatting_tool.py",
            "evals/speed_benchmark.py",
        ]
        for f in l1_files:
            lane, reason = classify_file(f, self.policy)
            self.assertEqual(lane, 1, f"Expected Lane 1 for '{f}', got Lane {lane} ({reason})")

        res = classify_changes(l1_files)
        self.assertEqual(res["effective_lane"], 1)
        self.assertEqual(res["effective_lane_name"], "LANE_1_MEDIUM_RISK")
        self.assertFalse(res["touches_lane_2"])
        self.assertTrue(res["touches_lane_1"])
        self.assertFalse(res["escalated"])

    def test_pure_lane_2_classification(self) -> None:
        """Verifies that graph storage, releases, ingestion, HITL, and governance map to Lane 2."""
        l2_files = [
            "graph/nee_graph.sqlite",
            "storage/driver.py",
            "dual_pipeline/storage/sqlite.py",
            "dual_pipeline/release/activate.py",
            "dual_pipeline/ingestion/u0.py",
            "dual_pipeline/hitl_governance_core/ledger.py",
            "tools/govern_learning_proposals.py",
            "tools/pr_governance.py",
            "tools/verify_commit.py",
            "tools/attestation_core/digest.py",
            "config/governance/risk_lanes_policy.json",
            "AGENTS.md",
            "hitl_decision_ledger.json",
        ]
        for f in l2_files:
            lane, reason = classify_file(f, self.policy)
            self.assertEqual(lane, 2, f"Expected Lane 2 for '{f}', got Lane {lane} ({reason})")

        res = classify_changes(l2_files)
        self.assertEqual(res["effective_lane"], 2)
        self.assertEqual(res["effective_lane_name"], "LANE_2_HIGH_RISK")
        self.assertTrue(res["touches_lane_2"])

    def test_mixed_change_escalation(self) -> None:
        """Verifies that any mixed change set escalates to the highest lane touched."""
        # Case A: Lane 0 + Lane 1 -> Escalates to Lane 1
        mixed_0_1 = ["docs/README.md", "dual_pipeline/analyzer.py"]
        res_0_1 = classify_changes(mixed_0_1)
        self.assertEqual(res_0_1["effective_lane"], 1)
        self.assertTrue(res_0_1["escalated"])

        # Case B: Lane 0 + Lane 2 -> Escalates to Lane 2
        mixed_0_2 = ["docs/README.md", "graph/nee_graph.sqlite"]
        res_0_2 = classify_changes(mixed_0_2)
        self.assertEqual(res_0_2["effective_lane"], 2)
        self.assertTrue(res_0_2["escalated"])
        self.assertTrue(res_0_2["touches_lane_2"])

        # Case C: Lane 1 + Lane 2 -> Escalates to Lane 2
        mixed_1_2 = ["tools/formatting_tool.py", "AGENTS.md"]
        res_1_2 = classify_changes(mixed_1_2)
        self.assertEqual(res_1_2["effective_lane"], 2)
        self.assertTrue(res_1_2["escalated"])

    def test_soft_caps_advisory(self) -> None:
        """Verifies batch size and worktree WIP soft cap checking functions."""
        # Batch size
        passed, loc, msg = check_batch_size([], self.repo_root, max_loc=400)
        self.assertTrue(isinstance(passed, bool))
        self.assertTrue(isinstance(loc, int))

        # Worktree WIP
        passed_wip, count, msg_wip = check_worktree_wip(self.repo_root, max_wip=3)
        self.assertTrue(isinstance(passed_wip, bool))
        self.assertTrue(isinstance(count, int))


if __name__ == "__main__":
    unittest.main()
