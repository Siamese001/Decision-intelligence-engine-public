"""Adversarial & Edge-Case Tests for Wave 1: PR Governance & Risk-Lane Classifier.

Covers mandatory edge-case categories:
1. Boundary payload (empty file list, root-only paths, traversal strings).
2. Malformed input (corrupted JSON policy, invalid git repo path).
3. Invariant bypass attempt (nested .md spoofing, root implementation_plan.md, orphan worktrees, stale manifest cache).
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.change_lane_classifier import (
    check_batch_size,
    check_worktree_wip,
    classify_changes,
    classify_file,
    load_policy,
)
from tools.verify_main_readiness import _check_cached_readiness


class TestPRGovernanceAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_path = ROOT / "config" / "governance" / "risk_lanes_policy.json"
        cls.policy = load_policy(cls.policy_path)

    def test_boundary_and_traversal_payloads_fail_closed(self) -> None:
        """Category 1 (Boundary payload): Empty strings and path traversal escalate to Lane 2."""
        for bad_path in ("", "   ", "../secret.md", "../../etc/passwd"):
            lane, _ = classify_file(bad_path, self.policy)
            self.assertEqual(lane, 2, f"Expected fail-closed Lane 2 for boundary path '{bad_path}'")

        empty_res = classify_changes([], policy_path=self.policy_path)
        self.assertEqual(empty_res["effective_lane"], 0)
        self.assertEqual(empty_res["file_count"], 0)

    def test_malformed_policy_and_invalid_repo_fail_closed(self) -> None:
        """Category 2 (Malformed input): Corrupted JSON policy and non-git directories fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bad_policy_file = tmp_p / "bad_policy.json"
            bad_policy_file.write_text("{corrupted_json:", encoding="utf-8")
            pol = load_policy(bad_policy_file)
            lane, _ = classify_file("README.md", pol)
            self.assertEqual(lane, 2)

            passed_batch, _, msg_batch = check_batch_size(["foo.py"], tmp_p)
            self.assertFalse(passed_batch)
            self.assertIn("failed closed", msg_batch)

            passed_wip, _, msg_wip = check_worktree_wip(tmp_p)
            self.assertFalse(passed_wip)
            self.assertIn("failed closed", msg_wip)

    def test_invariant_bypass_nested_markdown_and_root_plan_blocked(self) -> None:
        """Category 3 (Invariant bypass attempt): Nested unrecognized .md and root implementation_plan.md escalate to Lane 2."""
        lane_root_plan, _ = classify_file("implementation_plan.md", self.policy)
        self.assertEqual(lane_root_plan, 2)

        lane_nested_unknown, _ = classify_file("unrecognized_ stealth_folder/bypass.md", self.policy)
        self.assertEqual(lane_nested_unknown, 2)

        # Legitimate root README and docs/ plans remain Lane 0
        self.assertEqual(classify_file("README.md", self.policy)[0], 0)
        self.assertEqual(classify_file("docs/plans/feat_branch.md", self.policy)[0], 0)

    def test_invariant_bypass_stale_manifest_rejects_cached_readiness(self) -> None:
        """Category 3 (Invariant bypass attempt): _check_cached_readiness rejects cache hit when manifest is stale."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("tools.verify_main_readiness.validate_manifest_freshness", return_value=(False, "stale")):
                rc = _check_cached_readiness(
                    force_rerun=False,
                    use_cache=True,
                    recheck_failed_only=False,
                    head_sha="deadbeef",
                    branch_receipt_path=Path(tmp) / "receipt.json",
                    qualification_fingerprint="fp123",
                    readiness_started=0.0,
                    branch="feat/test",
                    update_global=False,
                    readiness_dir=Path(tmp),
                    manifest={"files": {}},
                )
                self.assertIsNone(rc)


    def test_worktree_wip_cap_default_is_30(self) -> None:
        """Verifies default max_wip cap is 30 in policy and check_worktree_wip."""
        self.assertEqual(self.policy.get("soft_caps", {}).get("max_active_worktrees"), 30)
        import inspect
        sig = inspect.signature(check_worktree_wip)
        self.assertEqual(sig.parameters["max_wip"].default, 30)


if __name__ == "__main__":
    unittest.main()
