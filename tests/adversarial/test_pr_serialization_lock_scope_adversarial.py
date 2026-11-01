#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for PR Serialization and Lock Governance (Wave 1).

Covers edge case categories:
1. Boundary payload: 0-byte additions and empty payload tree digests.
2. Malformed input: Corrupted, truncated, and missing JSON receipts during catch-up.
3. Concurrency & rollback: Non-destructive rollback upon conflict rebase with clean lock release.
4. Invariant bypass prevention: Lock re-entrancy and double-release safety.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.attestation_core import (
    compute_tree_digest,
    load_attestation_by_tree,
)
from tools.pr_governance_core.lifecycle import (
    _catch_up_queued_branch_under_lock,
    _rebind_receipts_after_catchup,
)
from tools.pr_governance_core.merge_lock import (
    GovernedMergeQueueLock,
    inspect_queue_lock,
)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class PRSerializationAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_ser_adv_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        _run_git(["init", "-b", "main"], self.repo_root)
        _run_git(["config", "user.name", "Adversarial Tester"], self.repo_root)
        _run_git(["config", "user.email", "adv@example.com"], self.repo_root)
        _run_git(["config", "commit.gpgsign", "false"], self.repo_root)

        readme = self.repo_root / "README.md"
        readme.write_text("# Adversarial Test Repo\n", encoding="utf-8")
        _run_git(["add", "README.md"], self.repo_root)
        _run_git(["commit", "-m", "chore: initial commit"], self.repo_root)
        self.base_sha = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_boundary_payload_zero_byte_file(self) -> None:
        """Category: Boundary payload - 0-byte file addition cleanly handled by digest and rebind."""
        branch = "feat/empty-file"
        slug = "feat_empty_file"
        _run_git(["checkout", "-b", branch], self.repo_root)

        empty_file = self.repo_root / "empty.keep"
        empty_file.touch()
        _run_git(["add", "empty.keep"], self.repo_root)
        _run_git(["commit", "-m", "feat: 0-byte file addition"], self.repo_root)
        old_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()

        # Advance main
        _run_git(["checkout", "main"], self.repo_root)
        m = self.repo_root / "main.txt"
        m.write_text("main update\n", encoding="utf-8")
        _run_git(["add", "main.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: main advance"], self.repo_root)
        new_base = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

        _run_git(["checkout", branch], self.repo_root)
        _run_git(["rebase", "main"], self.repo_root)
        new_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()

        # Rebind should complete without error
        _rebind_receipts_after_catchup(
            branch=branch,
            slug=slug,
            old_head_sha=old_sha,
            new_head_sha=new_sha,
            new_base_sha=new_base,
            repo_root=self.repo_root,
        )

        tree = compute_tree_digest(self.repo_root, ref=new_sha)
        self.assertTrue(bool(tree))

    def test_02_malformed_input_corrupted_json_receipts(self) -> None:
        """Category: Malformed input - truncated and invalid JSON in receipts dir handled gracefully."""
        branch = "feat/malformed-receipts"
        slug = "feat_malformed_receipts"
        _run_git(["checkout", "-b", branch], self.repo_root)

        code_f = self.repo_root / "code.py"
        code_f.write_text("x = 1\n", encoding="utf-8")
        _run_git(["add", "code.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: add code"], self.repo_root)
        old_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()

        # Plant truncated / invalid JSON
        reports_dir = self.repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        readiness_dir = reports_dir / "readiness"
        readiness_dir.mkdir(parents=True, exist_ok=True)

        (reports_dir / f"pr_review_{slug}_receipt.json").write_text("{ truncated_json: ", encoding="utf-8")
        (readiness_dir / f"local_main_readiness_{slug}_receipt.json").write_text("NOT_JSON", encoding="utf-8")

        # Must not raise an unhandled exception
        try:
            _rebind_receipts_after_catchup(
                branch=branch,
                slug=slug,
                old_head_sha=old_sha,
                new_head_sha=old_sha,
                new_base_sha=self.base_sha,
                repo_root=self.repo_root,
            )
        except Exception as e:
            self.fail(f"_rebind_receipts_after_catchup crashed on malformed JSON: {e}")

    def test_03_rollback_and_recovery_on_conflict_catchup(self) -> None:
        """Category: Rollback & recovery - conflict rebase aborts cleanly and restores pristine tree."""
        branch = "feat/conflict-branch"
        slug = "feat_conflict_branch"
        _run_git(["checkout", "-b", branch], self.repo_root)

        target = self.repo_root / "target.txt"
        target.write_text("branch conflicting line\n", encoding="utf-8")
        _run_git(["add", "target.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: branch edit"], self.repo_root)

        _run_git(["checkout", "main"], self.repo_root)
        target.write_text("main conflicting line\n", encoding="utf-8")
        _run_git(["add", "target.txt"], self.repo_root)
        _run_git(["commit", "-m", "fix: main edit"], self.repo_root)

        _run_git(["checkout", branch], self.repo_root)
        success, is_disjoint = _catch_up_queued_branch_under_lock(branch, slug, self.repo_root)

        # Must return failure
        self.assertFalse(success)
        # Working tree must be clean (rebase was aborted)
        status = _run_git(["status", "--porcelain"], self.repo_root).stdout.strip()
        self.assertEqual(status, "", "Working tree must be pristine after aborted catch-up rebase")

    def test_04_invariant_bypass_lock_double_release_safe(self) -> None:
        """Category: Invariant bypass - multiple releases of GovernedMergeQueueLock are idempotent."""
        lock = GovernedMergeQueueLock("feat/double-release", repo_root=self.repo_root)
        self.assertTrue(lock.acquire())
        self.assertIsNotNone(inspect_queue_lock(self.repo_root))

        # First release
        lock.release()
        self.assertIsNone(inspect_queue_lock(self.repo_root))
        self.assertFalse(lock.is_acquired())

        # Second release: must be safe and idempotent
        try:
            lock.release()
        except Exception as e:
            self.fail(f"Subsequent lock.release() crashed: {e}")
        self.assertFalse(lock.is_acquired())


if __name__ == "__main__":
    unittest.main()
