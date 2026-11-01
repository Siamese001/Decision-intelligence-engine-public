#!/usr/bin/env python3
"""
Adversarial tests for Git Workflow Consolidation.

Verifies that the consolidated 3-tier git architecture resists:
1. Malformed commit messages and dirty-dump evasion attempts.
2. Prohibited model naming tokens in commits.
3. Unverified pushes to origin when receipts are missing or corrupted.
4. Direct commits to main without hotfix authorization.
5. Staged syntax and linter violations.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_commit_message import validate_commit_message  # noqa: E402
from tools.verify_push_receipt import verify_push_receipt  # noqa: E402


class GitWorkflowAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT

    def test_01_adversary_evasion_in_commit_messages(self) -> None:
        evasive_messages = [
            "wip: quick checkpoint before lunch",
            "dirty: temp dump of working files",
            "chore: save files to branch",
            "feat(pipeline): save changes",
            "checkpoint dump of uncommitted state",
            "feat(ai): updated [claude] prompt",
            "fix(rag): optimize gpt-4 response latency",
            "refactor: introduce sol-max routing",
            "update logic",  # Missing conventional type
            ": missing type prefix",
            "feat(): empty scope header",
        ]
        for msg in evasive_messages:
            ok, reason = validate_commit_message(msg)
            self.assertFalse(ok, f"Adversarial commit message '{msg}' should have been rejected, got: {reason}")

    def test_02_adversary_push_without_valid_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_root, capture_output=True, check=True)

            # Create an unverified commit
            (tmp_root / "sample.txt").write_text("hello", encoding="utf-8")
            subprocess.run(["git", "add", "sample.txt"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "feat: unverified commit"], cwd=tmp_root, capture_output=True, check=True)

            # verify_push_receipt must reject because reports/ has no receipt
            passed, msg, dur = verify_push_receipt(tmp_root)
            self.assertFalse(passed, "Push must be rejected when no passing receipt exists")
            self.assertIn("No passing Tier 2 governance receipt", msg)

    def test_03_adversary_push_with_corrupted_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_root, capture_output=True, check=True)

            (tmp_root / "sample.txt").write_text("hello", encoding="utf-8")
            subprocess.run(["git", "add", "sample.txt"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "feat: unverified commit"], cwd=tmp_root, capture_output=True, check=True)

            reports = tmp_root / "reports"
            reports.mkdir(parents=True)
            (reports / "local_main_readiness_receipt.json").write_text(
                json.dumps({"terminal_outcome": "FAIL", "reason": "SECURITY_GATE_FAILURE"}),
                encoding="utf-8",
            )

            passed, msg, dur = verify_push_receipt(tmp_root)
            self.assertFalse(passed, "Push must be rejected when receipt terminal_outcome is FAIL")

    def test_04_direct_to_main_block_and_hotfix_override(self) -> None:
        # Pre-commit hook must reject direct commit to main unless GOVERNED_HOTFIX or GOVERNED_PR_MERGE
        pre_commit = ROOT / ".githooks" / "pre-commit"
        content = pre_commit.read_text(encoding="utf-8")
        self.assertIn("GOVERNED_HOTFIX", content)
        self.assertIn("GOVERNED_PR_MERGE", content)
        self.assertIn("Direct commits to '$CURRENT_BRANCH' are strictly prohibited", content)

    def test_05_adversary_merge_with_stale_mainline_rejected(self) -> None:
        """Adversarial merge attempt with stale review receipt after main advanced must fail-closed."""
        from tools.pr_governance_core.lifecycle import _validate_merge_receipt

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            run = lambda cmd: subprocess.run(cmd, cwd=tmp_root, capture_output=True, check=True, text=True)

            run(["git", "init", "-b", "main"])
            run(["git", "config", "user.name", "Tester"])
            run(["git", "config", "user.email", "test@example.com"])

            (tmp_root / "base.txt").write_text("initial base\n")
            run(["git", "add", "base.txt"])
            run(["git", "commit", "-m", "chore: initial commit"])

            # Fork feature branch
            run(["git", "checkout", "-b", "feat/adversary-stale"])
            (tmp_root / "feature.txt").write_text("feature code\n")
            run(["git", "add", "feature.txt"])
            run(["git", "commit", "-m", "feat: feature commit"])
            branch_tip = run(["git", "rev-parse", "HEAD"]).stdout.strip()

            # Seed a review receipt
            reports = tmp_root / "reports"
            reports.mkdir(parents=True)
            slug = "feat_adversary_stale"
            (reports / f"pr_review_{slug}_receipt.json").write_text(
                json.dumps({
                    "terminal_outcome": "PASS",
                    "head_sha": branch_tip,
                    "readiness_verified": True,
                    "clean_merge": True,
                }),
                encoding="utf-8",
            )

            # Switch back to main and advance it
            run(["git", "checkout", "main"])
            (tmp_root / "advancement.txt").write_text("mainline moved ahead\n")
            run(["git", "add", "advancement.txt"])
            run(["git", "commit", "-m", "feat: main advance"])

            # Validation must fail closed because base_ancestor != main_head_sha
            with unittest.mock.patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root):
                valid = _validate_merge_receipt("feat/adversary-stale", slug, branch_tip)
                self.assertFalse(valid, "Merge validation must reject when mainline has advanced past review receipt")

    def test_06_merge_lock_contention_and_delegation_bypass(self) -> None:
        """Adversarial lock contention raises LockContentionError, but supervisor delegation bypasses safely."""
        from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock, LockContentionError
        import os

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp_root, capture_output=True, check=True)

            lock1 = GovernedMergeQueueLock("feat/primary", repo_root=tmp_root, timeout_sec=30.0, non_blocking=True)
            self.assertTrue(lock1.acquire())

            # A second distinct branch attempting non-blocking acquire must fail
            lock2 = GovernedMergeQueueLock("feat/secondary", repo_root=tmp_root, timeout_sec=0.1, non_blocking=True)
            with self.assertRaises(LockContentionError):
                lock2.acquire()

            # A delegated child invocation with matching environment variable succeeds without deadlock
            child_lock = GovernedMergeQueueLock("feat/primary", repo_root=tmp_root, timeout_sec=0.1, non_blocking=True)
            with unittest.mock.patch.dict(os.environ, {"GOVERNED_MERGE_QUEUE_LOCK_BRANCH": "feat/primary"}):
                self.assertTrue(child_lock.acquire(), "Delegated lock acquisition must succeed without contention")
                child_lock.release()

            lock1.release()


if __name__ == "__main__":
    unittest.main()
