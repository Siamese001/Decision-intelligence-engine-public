"""Adversarial tests for PR readiness receipt ancestor binding and tamper resistance."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pr_governance_core.inspection import _check_readiness_receipt_pass


class ReceiptBindingAdversarialTests(unittest.TestCase):
    """Adversarial validation asserting receipts must bind strictly to the exact HEAD commit."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.slug = "feat_adversarial_test"
        self.branch = "feat/adversarial-test"
        self.receipt_dir = self.root / "reports" / "readiness"
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        self.receipt_path = self.receipt_dir / f"local_main_readiness_{self.slug}_receipt.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_receipt(self, sha: str, branch: str, outcome: str = "PASS") -> None:
        data = {
            "head_commit_sha": sha,
            "branch": branch,
            "terminal_outcome": outcome,
            "timestamp": "2026-10-04T12:00:00Z",
        }
        self.receipt_path.write_text(json.dumps(data), encoding="utf-8")

    def test_exact_sha_and_branch_match_passes(self) -> None:
        """Exact matching commit SHA and branch name passes readiness verification."""
        sha = "a1b2c3d4e5f6789012345678901234567890abcd"
        self._write_receipt(sha=sha, branch=self.branch, outcome="PASS")

        with patch("tools.pr_governance_core.inspection.ROOT", self.root):
            passed = _check_readiness_receipt_pass(self.branch, sha, self.slug)
        self.assertTrue(passed)

    def test_stale_ancestor_sha_is_strictly_rejected(self) -> None:
        """Adversarial attempt: committing on top of a passing receipt fails verification."""
        ancestor_sha = "1111111111111111111111111111111111111111"
        head_sha = "2222222222222222222222222222222222222222"
        # Receipt was generated for ancestor commit
        self._write_receipt(sha=ancestor_sha, branch=self.branch, outcome="PASS")

        with patch("tools.pr_governance_core.inspection.ROOT", self.root):
            passed = _check_readiness_receipt_pass(self.branch, head_sha, self.slug)
        self.assertFalse(passed, "Stale ancestor receipt was accepted! Strict binding violation.")

    def test_mismatched_branch_name_is_rejected(self) -> None:
        """Receipt for another branch with identical commit SHA fails branch binding."""
        sha = "a1b2c3d4e5f6789012345678901234567890abcd"
        self._write_receipt(sha=sha, branch="feat/other-feature", outcome="PASS")

        with patch("tools.pr_governance_core.inspection.ROOT", self.root):
            passed = _check_readiness_receipt_pass(self.branch, sha, self.slug)
        self.assertFalse(passed, "Receipt with mismatched branch was accepted!")

    def test_failed_outcome_receipt_is_rejected(self) -> None:
        """A terminal outcome other than PASS is rejected even if SHA matches."""
        sha = "a1b2c3d4e5f6789012345678901234567890abcd"
        self._write_receipt(sha=sha, branch=self.branch, outcome="FAIL")

        with patch("tools.pr_governance_core.inspection.ROOT", self.root):
            passed = _check_readiness_receipt_pass(self.branch, sha, self.slug)
        self.assertFalse(passed, "Non-passing receipt was accepted!")

    def test_head_wildcard_bypass_prevented(self) -> None:
        """Adversarial attempt to pass 'HEAD' branch wildcard is rejected."""
        sha = "a1b2c3d4e5f6789012345678901234567890abcd"
        self._write_receipt(sha=sha, branch="HEAD", outcome="PASS")

        with patch("tools.pr_governance_core.inspection.ROOT", self.root):
            passed = _check_readiness_receipt_pass(self.branch, sha, self.slug)
        self.assertFalse(passed, "Wildcard HEAD receipt was accepted for named branch!")


if __name__ == "__main__":
    unittest.main()
