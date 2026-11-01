#!/usr/bin/env python3
"""Unit and Integration Tests for Lock-Free Pre-Qualification & DSSE Readiness Rebinding (Wave 1).

Verifies:
1. _rebind_receipts_after_catchup re-seals both PR review and out-of-tree DSSE readiness attestations.
2. check_readiness_qualification verifies the rebound DSSE attestation without re-running readiness gates.
3. _catch_up_queued_branch_under_lock accurately distinguishes disjoint vs overlapping mainline advances.
4. land_branch_serialized performs rebase and qualification outside GovernedMergeQueueLock.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.attestation_core import (
    READINESS_PREDICATE_TYPE,
    check_readiness_qualification,
    compute_tree_digest,
    save_readiness_attestation,
)
from tools.pr_governance_core.lifecycle import (
    _catch_up_queued_branch_under_lock,
    _rebind_receipts_after_catchup,
    _seal_readiness_attestation,
)
from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock
from tools.rebase_orchestrator import land_branch_serialized


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class PRSerializationLockScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_ser_lock_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        _run_git(["init", "-b", "main"], self.repo_root)
        _run_git(["config", "user.name", "Lock Scope Test"], self.repo_root)
        _run_git(["config", "user.email", "locktest@example.com"], self.repo_root)
        _run_git(["config", "commit.gpgsign", "false"], self.repo_root)

        # Base commit on main
        readme = self.repo_root / "README.md"
        readme.write_text("# Base Repository\n", encoding="utf-8")
        _run_git(["add", "README.md"], self.repo_root)
        _run_git(["commit", "-m", "chore: initial commit"], self.repo_root)
        self.base_sha = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_rebind_receipts_after_catchup_seals_dsse_readiness(self) -> None:
        """Verifies _rebind_receipts_after_catchup updates review and seals out-of-tree DSSE readiness."""
        branch = "feat/disjoint-work"
        slug = "feat_disjoint_work"
        _run_git(["checkout", "-b", branch], self.repo_root)

        feat_file = self.repo_root / "feature.py"
        feat_file.write_text("# feature code\n", encoding="utf-8")
        _run_git(["add", "feature.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: add feature file"], self.repo_root)
        old_head_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()
        old_tree_digest = compute_tree_digest(self.repo_root, ref=old_head_sha)

        # Plant initial review receipt and readiness receipt
        reports_dir = self.repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        readiness_dir = reports_dir / "readiness"
        readiness_dir.mkdir(parents=True, exist_ok=True)

        rev_receipt = {
            "branch": branch,
            "head_sha": old_head_sha,
            "base_commit_sha": self.base_sha,
            "terminal_outcome": "PASS",
        }
        (reports_dir / f"pr_review_{slug}_receipt.json").write_text(
            json.dumps(rev_receipt, indent=2), encoding="utf-8"
        )

        readiness_receipt = {
            "branch": branch,
            "head_commit_sha": old_head_sha,
            "base_commit_sha": self.base_sha,
            "terminal_outcome": "PASS",
            "total_stages": 32,
        }
        (readiness_dir / f"local_main_readiness_{slug}_receipt.json").write_text(
            json.dumps(readiness_receipt, indent=2), encoding="utf-8"
        )

        # Plant old out-of-tree DSSE readiness attestation
        save_readiness_attestation(
            repo_root=self.repo_root,
            predicate=readiness_receipt,
            tree_digest=old_tree_digest,
            head_sha=old_head_sha,
        )

        # Advance main disjointly
        _run_git(["checkout", "main"], self.repo_root)
        main_file = self.repo_root / "docs_update.md"
        main_file.write_text("# Doc update on main\n", encoding="utf-8")
        _run_git(["add", "docs_update.md"], self.repo_root)
        _run_git(["commit", "-m", "docs: update on main"], self.repo_root)
        new_base_sha = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

        # Rebase branch onto new main
        _run_git(["checkout", branch], self.repo_root)
        _run_git(["rebase", "main"], self.repo_root)
        new_head_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()
        new_tree_digest = compute_tree_digest(self.repo_root, ref=new_head_sha)

        # Call _rebind_receipts_after_catchup
        _rebind_receipts_after_catchup(
            branch=branch,
            slug=slug,
            old_head_sha=old_head_sha,
            new_head_sha=new_head_sha,
            new_base_sha=new_base_sha,
            repo_root=self.repo_root,
        )

        # 1. Independent check: Review receipt updated
        rev_data = json.loads((reports_dir / f"pr_review_{slug}_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(rev_data["head_sha"], new_head_sha)
        self.assertEqual(rev_data["base_commit_sha"], new_base_sha)

        # 2. Independent check: Out-of-tree DSSE readiness attestation exists for new_tree_digest
        att_path = self.repo_root / ".local" / "receipts" / "readiness" / f"{new_tree_digest}.json"
        self.assertTrue(att_path.is_file(), f"Expected DSSE attestation at {att_path}")

        # 3. Independent check: check_readiness_qualification passes via DSSE attestation
        is_passed, stmt, desc = check_readiness_qualification(
            repo_root=self.repo_root,
            branch=branch,
            head_sha=new_head_sha,
            slug=slug,
        )
        self.assertTrue(is_passed, "Readiness qualification should pass after catchup rebind")
        self.assertIn("DSSE attestation", desc)
        self.assertEqual(stmt["predicate"]["head_commit_sha"], new_head_sha)

    def test_02_catch_up_queued_branch_disjoint_returns_true(self) -> None:
        """Verifies _catch_up_queued_branch_under_lock handles disjoint mainline advance cleanly."""
        branch = "feat/disjoint-advance"
        slug = "feat_disjoint_advance"
        _run_git(["checkout", "-b", branch], self.repo_root)

        f = self.repo_root / "disjoint.py"
        f.write_text("print('disjoint')\n", encoding="utf-8")
        _run_git(["add", "disjoint.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: disjoint addition"], self.repo_root)
        b_sha = _run_git(["rev-parse", branch], self.repo_root).stdout.strip()

        # Advance main
        _run_git(["checkout", "main"], self.repo_root)
        m = self.repo_root / "main_disjoint.txt"
        m.write_text("main update\n", encoding="utf-8")
        _run_git(["add", "main_disjoint.txt"], self.repo_root)
        _run_git(["commit", "-m", "chore: main advancement"], self.repo_root)

        # Call catch up under lock
        success, is_disjoint = _catch_up_queued_branch_under_lock(branch, slug, self.repo_root)
        self.assertTrue(success)
        self.assertTrue(is_disjoint)

    def test_03_catch_up_queued_branch_overlapping_detects_overlap(self) -> None:
        """Verifies _catch_up_queued_branch_under_lock flags overlapping mainline change for re-review."""
        branch = "feat/overlap-work"
        slug = "feat_overlap_work"
        _run_git(["checkout", "-b", branch], self.repo_root)

        shared = self.repo_root / "shared.txt"
        shared.write_text("line 1\nline 2 on branch\n", encoding="utf-8")
        _run_git(["add", "shared.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: branch edit shared.txt"], self.repo_root)

        # Advance main touching the same file
        _run_git(["checkout", "main"], self.repo_root)
        shared.write_text("line 1 on main\nline 2\n", encoding="utf-8")
        _run_git(["add", "shared.txt"], self.repo_root)
        _run_git(["commit", "-m", "feat: main edit shared.txt"], self.repo_root)

        # Attempt catch up
        success, is_disjoint = _catch_up_queued_branch_under_lock(branch, slug, self.repo_root)
        # Even if git rebase succeeded without conflict, disjointness check must return False
        self.assertFalse(is_disjoint, "Overlapping file edits must return is_disjoint=False")

    def test_04_land_branch_serialized_executes_qualification_outside_lock(self) -> None:
        """Verifies land_branch_serialized performs rebase and qualification outside queue lock."""
        branch = "feat/land-test"
        _run_git(["checkout", "-b", branch], self.repo_root)

        c = self.repo_root / "land.py"
        c.write_text("# land code\n", encoding="utf-8")
        _run_git(["add", "land.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: land code"], self.repo_root)

        lock_acquire_times: list[float] = []

        class MonitoredLock(GovernedMergeQueueLock):
            def acquire(self) -> bool:
                lock_acquire_times.append(time.monotonic())
                return super().acquire()

        qual_called_times: list[float] = []

        def mock_qual(br: str, wd: Path) -> bool:
            qual_called_times.append(time.monotonic())
            return True

        mock_merge_proc = subprocess.CompletedProcess(args=["merge"], returncode=0, stdout="merged", stderr="")

        with (
            patch("tools.rebase_orchestrator.GovernedMergeQueueLock", MonitoredLock),
            patch("tools.rebase_orchestrator._run_readiness_qualification", side_effect=mock_qual),
            patch("tools.rebase_orchestrator._run_pr_merge", return_value=mock_merge_proc),
            patch("tools.rebase_orchestrator.ROOT", self.repo_root),
        ):
            res = land_branch_serialized(
                branch=branch,
                title="feat: land branch test",
                repo_root=self.repo_root,
            )

        self.assertEqual(res.status, "SUCCESS")
        self.assertTrue(len(qual_called_times) > 0, "Readiness qualification should have been called")
        self.assertTrue(len(lock_acquire_times) > 0, "Merge queue lock should have been acquired")
        # Qualification must happen BEFORE lock acquisition
        self.assertLess(
            qual_called_times[0],
            lock_acquire_times[0],
            "Readiness qualification MUST execute BEFORE acquiring GovernedMergeQueueLock",
        )


if __name__ == "__main__":
    unittest.main()
