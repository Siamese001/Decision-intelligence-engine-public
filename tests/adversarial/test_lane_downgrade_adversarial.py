"""tests/adversarial/test_lane_downgrade_adversarial.py

Adversarial test suite for Change Lane Downgrade Prevention (Wave 7 / Acceptance Criteria).
Verifies:
1. Path traversal attacks (e.g. docs/../graph/nee_graph.sqlite) cannot downgrade Lane 2 files.
2. Camouflage attacks (100 docs files + 1 Lane 2 file) strictly escalate to Lane 2.
3. Modifying policy-as-code itself (config/governance/risk_lanes_policy.json) cannot be downgraded.
4. Modifying the classifier itself (tools/change_lane_classifier.py) cannot be downgraded.
5. Unknown/unrecognized file types strictly fail closed to Lane 2.
6. Empty or invalid policies fail closed to Lane 2.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from tools.change_lane_classifier import (
    classify_changes,
    classify_file,
    load_policy,
)


class LaneDowngradeAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.policy = load_policy(cls.repo_root / "config/governance/risk_lanes_policy.json")

    def test_path_traversal_downgrade_attempt(self) -> None:
        """Verifies that sneaky relative paths attempting to hide inside docs/ resolve to Lane 2."""
        traversal_attempts = [
            "docs/../graph/nee_graph.sqlite",
            "documentation/../../storage/driver.py",
            "tests/../AGENTS.md",
            "./hitl_decision_ledger.json",
        ]
        for p in traversal_attempts:
            # Normalize path as standard filesystem resolution does
            norm = str(Path(p))
            lane, _ = classify_file(norm, self.policy)
            self.assertEqual(lane, 2, f"Path traversal attempt '{p}' must resolve to Lane 2")

    def test_camouflage_bundle_attack(self) -> None:
        """Verifies that bundling 100 Lane 0 files with 1 Lane 2 file cannot dilute or downgrade."""
        many_docs = [f"docs/notes/page_{i}.md" for i in range(100)]
        sneaky_payload = many_docs + ["tools/verify_commit.py"]

        result = classify_changes(sneaky_payload)
        self.assertEqual(result["effective_lane"], 2, "Camouflage bundle MUST escalate to Lane 2")
        self.assertTrue(result["touches_lane_2"])
        self.assertTrue(result["escalated"])
        self.assertEqual(result["lane_counts"][0], 100)
        self.assertEqual(result["lane_counts"][2], 1)

    def test_policy_tamper_cannot_downgrade_self(self) -> None:
        """Verifies that risk_lanes_policy.json itself is strictly classified as Lane 2."""
        lane, reason = classify_file("config/governance/risk_lanes_policy.json", self.policy)
        self.assertEqual(lane, 2)
        self.assertIn("Lane 2", reason)

    def test_classifier_tamper_cannot_downgrade_self(self) -> None:
        """Verifies that tools/change_lane_classifier.py itself is strictly classified as Lane 2."""
        lane, reason = classify_file("tools/change_lane_classifier.py", self.policy)
        self.assertEqual(lane, 2)
        self.assertIn("Lane 2", reason)

    def test_unrecognized_paths_fail_closed(self) -> None:
        """Verifies that any foreign, novel, or malformed path defaults to Lane 2."""
        unknown_paths = [
            "kernel/security/module.c",
            "etc/shadow",
            "var/log/syslog.1",
            "untracked_new_subsystem/code.rs",
            "root_secret.key",
        ]
        for p in unknown_paths:
            lane, reason = classify_file(p, self.policy)
            self.assertEqual(lane, 2, f"Unrecognized path '{p}' must fail closed to Lane 2")
            self.assertIn("fail-closed", reason.lower())

    def test_empty_or_broken_policy_fails_closed(self) -> None:
        """Verifies that an empty or missing policy still defaults safely to Lane 2."""
        empty_policy: dict = {}
        lane, reason = classify_file("any_random_file.py", empty_policy)
        self.assertEqual(lane, 2)


if __name__ == "__main__":
    unittest.main()
