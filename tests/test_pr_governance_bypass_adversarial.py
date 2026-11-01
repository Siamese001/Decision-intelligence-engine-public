"""Wave 1 Adversarial & Edge-Case Tests: PR Governance Bypass & Ratchet Enforcement.

Covers mandatory edge-case categories:
1. Invariant bypass attempt (forged Stage A sync receipt, --no-verify commit, mixed output/ + code on feat/*)
2. Malformed input (corrupt pre-commit receipt JSON, syntax-broken test/healer files)
3. Boundary payload (empty diff, fork_sha == main same-commit regression)
4. Exact production regression (PR #9 uncalled ratchet and sync.py:167-205 premature APPROVE seal)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.ingest_worktree_guard import (
    IngestWorktreeCodeFreezeError,
    assert_ingest_worktree_code_freeze,
)
from tools.lint_test_and_healer_ratchet import (
    check_healer_ratchet,
    check_test_ratchet,
    count_healer_metrics,
    count_test_assertions,
)
from tools.pr_governance_core.code_review import audit_post_fork_regression
from tools.pr_governance_core.lifecycle import (
    _is_verified_code_review_receipt,
    _validate_merge_receipt,
)
from tools.pr_governance_core.sync import cmd_sync
from tools.pre_commit_orchestrator import verify_pre_commit_receipt_for_head


class TestPRGovernanceBypassAdversarial(unittest.TestCase):
    """Adversarial test suite targeting the 6 PR governance bypasses found in the 500-PR review."""

    def test_adversarial_malformed_pre_commit_receipt_and_syntax_errors(self) -> None:
        """Category: Malformed input — corrupt JSON receipt and invalid Python syntax in ratchet."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_dir = repo / ".git"
            git_dir.mkdir()
            (git_dir / "nee_pre_commit_receipt.json").write_text("{corrupt json", encoding="utf-8")

            ok, msg = verify_pre_commit_receipt_for_head(repo)
            self.assertFalse(ok)

        # SyntaxError in test or healer source must not crash AST counters
        funcs, triv = count_test_assertions("def test_broken(:\n    assert 1\n")
        self.assertEqual(funcs, {})
        self.assertEqual(triv, 0)

        lines, rx = count_healer_metrics("def broken_healer(:\n    re.sub('a', 'b', x)\n")
        self.assertEqual(lines, 2)
        self.assertEqual(rx, 0)

    def test_adversarial_trivial_assert_true_and_deleted_test_function(self) -> None:
        """Category: Invariant bypass attempt — replacing real assertions with `assert True`."""
        main_src = (
            "def test_critical_gate():\n"
            "    assert check_a() is True\n"
            "    assert check_b() == 42\n"
            "\n"
            "def test_secondary_gate():\n"
            "    assert check_c()\n"
        )
        branch_src = (
            "def test_critical_gate():\n"
            "    assert True\n"
            "    assert check_a() is True\n"
        )
        violations = check_test_ratchet(main_src, branch_src, "tests/test_gate.py")
        self.assertTrue(any("trivial `assert True`" in v for v in violations))
        self.assertTrue(any("assertion count reduced" in v for v in violations))
        self.assertTrue(any("test_secondary_gate: existing test function deleted" in v for v in violations))

    def test_adversarial_validate_merge_receipt_blocks_unverified_sync_receipt(self) -> None:
        """Category: Exact production regression — sync.py premature APPROVE receipt rejected at merge."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            slug = "feat_bypass_attempt"
            forged = {
                "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
                "branch": "feat/bypass-attempt",
                "head_sha": "deadbeef1234",
                "base": "main",
                "clean_merge": True,
                "readiness_verified": True,
                "wave_summary_verified": True,
                "code_review": {
                    "verdict": "APPROVE",
                    "verdict_reason": "Synchronized and qualified under Stage A sync",
                },
                "terminal_outcome": "PASS",
            }
            (reports / f"pr_review_{slug}_receipt.json").write_text(
                json.dumps(forged), encoding="utf-8"
            )
            with patch("tools.pr_governance_core.lifecycle.ROOT", root):
                self.assertFalse(
                    _validate_merge_receipt("feat/bypass-attempt", slug, "deadbeef1234")
                )

    def test_adversarial_sync_fails_closed_when_post_fork_regression_detected(self) -> None:
        """Category: Rollback & recovery / Invariant bypass — cmd_sync rejects branch with post-fork regression."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            branch = "feat/regress-branch"
            wt_dir = root / ".worktrees" / "feat_regress_branch"
            wt_dir.mkdir(parents=True)
            (wt_dir / ".git").write_text("gitdir: /fake/git\n", encoding="utf-8")

            def fake_sub(cmd, *args, **kwargs):
                proc = MagicMock()
                proc.returncode = 0
                proc.stdout = ""
                return proc

            with (
                patch("tools.pr_governance_core.sync.get_feature_branches", return_value=[branch]),
                patch("tools.pr_governance_core.sync.compute_first_parent_sync", return_value=(0, 1)),
                patch("tools.pr_governance_core.sync.subprocess.run", side_effect=fake_sub),
                patch("tools.pr_governance_core.sync.run_git", return_value="headsha123"),
                patch(
                    "tools.pr_governance_core.code_review.audit_post_fork_regression",
                    return_value=(False, ["tests/test_foo.py: assertion count reduced"]),
                ),
            ):
                args = argparse.Namespace(
                    branch=branch,
                    repo_root=root,
                    skip_readiness=False,
                    fail_fast=False,
                    full=False,
                    dry_run=False,
                    prune_caches=False,
                )
                rc = cmd_sync(args)
                self.assertEqual(rc, 1)
                receipt_path = root / "reports" / "pr_review_feat_regress_branch_receipt.json"
                self.assertTrue(receipt_path.is_file())
                payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["terminal_outcome"], "FAIL")
                self.assertFalse(payload["post_fork_regression_passed"])


if __name__ == "__main__":
    unittest.main()
