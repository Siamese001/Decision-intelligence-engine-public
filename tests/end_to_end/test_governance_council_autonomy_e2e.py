"""tests/end_to_end/test_governance_council_autonomy_e2e.py

End-to-end test suite for 6-Agent PR Governance Council deliberative reasoning,
consensus scoring, bounded multi-turn debate, and action item hardening.
"""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict

from tools.pr_governance_core.council import (
    CouncilRole,
    deliberate_branch,
    deliberate_council_queue,
    harden_action_item,
)


class TestGovernanceCouncilAutonomyE2E(unittest.TestCase):
    """E2E suite verifying 6-agent deliberative council reasoning and action hardening."""

    def test_e2e_council_deliberation_all_six_roles_and_consensus_score(self):
        """Verifies multi-agent evaluation across all 6 roles with consensus scoring."""
        ready_branch: Dict[str, Any] = {
            "branch": "feat/clean-ready-feature",
            "ahead": 2,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P0",
            "churn": 60,
            "file_count": 2,
            "changed_files": ["tools/core.py", "tests/test_core.py"],
            "readiness_receipt_pass": True,
        }
        res_ready = deliberate_branch(ready_branch, turn_budget=3)
        self.assertEqual(res_ready.branch, "feat/clean-ready-feature")
        self.assertEqual(len(res_ready.findings), 6)
        for role in CouncilRole:
            self.assertIn(role.value, res_ready.findings)
        self.assertGreaterEqual(res_ready.consensus_score, 0.85)
        self.assertIn("Execute 30-gate PR review", res_ready.hardened_action)
        self.assertLessEqual(res_ready.turns_deliberated, 3)

        # Security hazard branch
        security_branch: Dict[str, Any] = {
            "branch": "feat/hazard-credentials",
            "ahead": 1,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P0",
            "changed_files": ["storage/credentials.py", "env_di"],
        }
        res_sec = deliberate_branch(security_branch, turn_budget=3)
        self.assertEqual(len(res_sec.findings), 6)
        sec_findings = res_sec.findings[CouncilRole.SECURITY.value]
        self.assertEqual(sec_findings[0].verdict, "OBJECT")
        self.assertEqual(sec_findings[0].score, 0.0)
        self.assertLessEqual(res_sec.consensus_score, 0.15)
        self.assertIn("tools/scan_credential_leaks.py", sec_findings[0].hardened_requirement)

        # Conflict branch
        conflict_branch: Dict[str, Any] = {
            "branch": "feat/conflicting-pipeline",
            "ahead": 3,
            "behind": 8,
            "clean_merge": False,
            "dirty": False,
            "tier": "P1",
            "rebase_effort": "HIGH",
        }
        res_conflict = deliberate_branch(conflict_branch, turn_budget=3)
        topo_findings = res_conflict.findings[CouncilRole.TOPOLOGY.value]
        self.assertEqual(topo_findings[0].verdict, "OBJECT")
        self.assertIn("rebase", res_conflict.hardened_action.lower())
        self.assertLess(res_conflict.consensus_score, res_ready.consensus_score)

    def test_e2e_council_queue_sequence_and_debate_rationales(self):
        """Verifies deterministic merge sequencing and pairwise debate rationales."""
        queue = [
            {
                "branch": "feat/ready-alpha",
                "ahead": 1,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "tier": "P0",
                "churn": 45,
                "file_count": 2,
                "readiness_receipt_pass": True,
            },
            {
                "branch": "feat/waves-beta",
                "ahead": 2,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "tier": "P1",
                "open_waves": "Wave 3",
            },
            {
                "branch": "feat/conflict-gamma",
                "ahead": 5,
                "behind": 12,
                "clean_merge": False,
                "dirty": False,
                "tier": "P1",
            },
        ]
        deliberation = deliberate_council_queue(queue, turn_budget=3)
        self.assertEqual(len(deliberation.assessments), 3)
        self.assertEqual(len(deliberation.debate_rationales), 2)

        # Pairwise rationale inspection
        r1 = deliberation.debate_rationales[0]
        self.assertIn("Merge #1 `feat/ready-alpha`", r1)
        self.assertIn("before #2 `feat/waves-beta`", r1)
        self.assertIn("Cur disp=", r1)

        r2 = deliberation.debate_rationales[1]
        self.assertIn("Merge #2 `feat/waves-beta`", r2)
        self.assertIn("before #3 `feat/conflict-gamma`", r2)

    def test_e2e_hardened_action_zero_vague_placeholders(self):
        """Verifies that all action hardening rejects vague non-actionable placeholders."""
        branch_item = {
            "branch": "feat/edge-case",
            "ahead": 0,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P2",
        }
        vague_samples = [
            "Review and proceed",
            "review and proceed",
            "Fix tests",
            "Work in progress",
            "Triage",
            "continue waves",
            "rebase and pr",
        ]
        for v in vague_samples:
            hardened = harden_action_item(branch_item, v, {})
            self.assertNotIn("Review and proceed", hardened)
            self.assertNotIn("Work in progress", hardened)
            self.assertNotIn("Triage", hardened)
            self.assertGreater(len(hardened), 10)
            self.assertTrue(
                "tools/pr_governance.py" in hardened or "rebase" in hardened.lower() or "wave" in hardened.lower()
            )

    def test_e2e_council_deliberation_json_serialization(self):
        """Verifies RFC-8259 JSON serialization of council deliberation results."""
        queue = [
            {
                "branch": "feat/serial-ready",
                "ahead": 1,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "tier": "P0",
                "churn": 20,
                "file_count": 1,
            }
        ]
        delib = deliberate_council_queue(queue, turn_budget=3)
        as_dict = delib.to_dict()
        self.assertIn("timestamp", as_dict)
        self.assertIn("assessments", as_dict)
        self.assertIn("merge_sequence", as_dict)
        self.assertIn("debate_rationales", as_dict)

        # RFC-8259 serialize/deserialize roundtrip
        json_str = json.dumps(as_dict)
        roundtrip = json.loads(json_str)
        self.assertEqual(len(roundtrip["assessments"]), 1)
        assm = roundtrip["assessments"][0]
        self.assertIn("consensus_score", assm)
        self.assertIn("role_scores", assm)
        self.assertIn("turns_deliberated", assm)


if __name__ == "__main__":
    unittest.main()
