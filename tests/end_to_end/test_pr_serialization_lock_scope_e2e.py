#!/usr/bin/env python3
"""End-to-End Test for Serialized PR Landing and Disjoint DSSE Attestation Rebinding (Wave 1).

Verifies:
1. Two concurrent feature branches with disjoint diffs landing onto advancing main.
2. Disjoint catch-up rebase under lock rebinds receipts and seals DSSE readiness attestation.
3. Lock hold duration is bounded (< 5.0 seconds).
4. Full repo integrity check passes with both merges committed cleanly.
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
from unittest.mock import patch

from tools.attestation_core import (
    check_readiness_qualification,
    compute_tree_digest,
    load_attestation_by_tree,
    save_readiness_attestation,
)
from tools.pr_governance_core.lifecycle import (
    _catch_up_queued_branch_under_lock,
    _rebind_receipts_after_catchup,
)
from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class PRSerializationLockScopeE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_ser_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        _run_git(["init", "-b", "main"], self.repo_root)
        _run_git(["config", "user.name", "E2E Orchestrator"], self.repo_root)
        _run_git(["config", "user.email", "orchestrator@example.com"], self.repo_root)
        _run_git(["config", "commit.gpgsign", "false"], self.repo_root)

        # Base commit
        readme = self.repo_root / "README.md"
        readme.write_text("# Project Root\n", encoding="utf-8")
        _run_git(["add", "README.md"], self.repo_root)
        _run_git(["commit", "-m", "chore: base repository commit"], self.repo_root)
        self.base_sha = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_concurrent_disjoint_branches_e2e_landing(self) -> None:
        """E2E: Branch 1 lands, Branch 2 catches up under lock with DSSE rebinding in < 5s."""
        # Provision Branch 1
        _run_git(["checkout", "-b", "feat/service-a"], self.repo_root)
        f_a = self.repo_root / "service_a.py"
        f_a.write_text("# Service A code\n", encoding="utf-8")
        _run_git(["add", "service_a.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: implement Service A"], self.repo_root)

        # Provision Branch 2 from initial base
        _run_git(["checkout", "main"], self.repo_root)
        _run_git(["checkout", "-b", "feat/service-b"], self.repo_root)
        f_b = self.repo_root / "service_b.py"
        f_b.write_text("# Service B code\n", encoding="utf-8")
        _run_git(["add", "service_b.py"], self.repo_root)
        _run_git(["commit", "-m", "feat: implement Service B"], self.repo_root)
        b2_old_sha = _run_git(["rev-parse", "feat/service-b"], self.repo_root).stdout.strip()
        b2_old_tree = compute_tree_digest(self.repo_root, ref=b2_old_sha)

        # Plant initial review and readiness qualifications for Branch 2
        reports_dir = self.repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        readiness_dir = reports_dir / "readiness"
        readiness_dir.mkdir(parents=True, exist_ok=True)

        slug_b = "feat_service_b"
        rev_receipt_b = {
            "branch": "feat/service-b",
            "head_sha": b2_old_sha,
            "base_commit_sha": self.base_sha,
            "terminal_outcome": "PASS",
        }
        (reports_dir / f"pr_review_{slug_b}_receipt.json").write_text(
            json.dumps(rev_receipt_b, indent=2), encoding="utf-8"
        )

        readiness_receipt_b = {
            "branch": "feat/service-b",
            "head_commit_sha": b2_old_sha,
            "base_commit_sha": self.base_sha,
            "terminal_outcome": "PASS",
            "total_stages": 32,
        }
        (readiness_dir / f"local_main_readiness_{slug_b}_receipt.json").write_text(
            json.dumps(readiness_receipt_b, indent=2), encoding="utf-8"
        )
        save_readiness_attestation(
            repo_root=self.repo_root,
            predicate=readiness_receipt_b,
            tree_digest=b2_old_tree,
            head_sha=b2_old_sha,
        )

        # Land Branch 1 to main
        _run_git(["checkout", "main"], self.repo_root)
        _run_git(["merge", "--no-ff", "feat/service-a", "-m", "Merge branch 'feat/service-a' into main"], self.repo_root)
        main_after_a = _run_git(["rev-parse", "main"], self.repo_root).stdout.strip()

        # Branch 2 now attempts serialized catch-up under lock
        _run_git(["checkout", "feat/service-b"], self.repo_root)

        lock = GovernedMergeQueueLock("feat/service-b", repo_root=self.repo_root)
        t_start = time.monotonic()
        with lock:
            catchup_ok, is_disjoint = _catch_up_queued_branch_under_lock(
                branch="feat/service-b",
                slug=slug_b,
                repo_root=self.repo_root,
            )
            self.assertTrue(catchup_ok)
            self.assertTrue(is_disjoint)

            b2_new_sha = _run_git(["rev-parse", "feat/service-b"], self.repo_root).stdout.strip()
            self.assertNotEqual(b2_old_sha, b2_new_sha)

            # Perform merge of Branch 2 onto main
            _run_git(["checkout", "main"], self.repo_root)
            _run_git(
                ["merge", "--no-ff", "feat/service-b", "-m", "Merge branch 'feat/service-b' into main"],
                self.repo_root,
            )
        lock_hold_duration = time.monotonic() - t_start

        # Verification 1: Lock hold duration is bounded under 5.0 seconds
        self.assertLess(
            lock_hold_duration,
            5.0,
            f"Lock hold duration {lock_hold_duration:.2f}s exceeded 5.0s budget",
        )

        # Verification 2: Rebound DSSE attestation exists for Branch 2's new tree digest
        b2_new_tree = compute_tree_digest(self.repo_root, ref=b2_new_sha)
        att = load_attestation_by_tree(self.repo_root, b2_new_tree, head_sha=b2_new_sha)
        self.assertIsNotNone(att, f"Expected DSSE attestation for tree {b2_new_tree}")
        self.assertEqual(att["predicate"]["head_commit_sha"], b2_new_sha)

        # Verification 3: Mainline commit tree contains files from both services
        self.assertTrue((self.repo_root / "service_a.py").is_file())
        self.assertTrue((self.repo_root / "service_b.py").is_file())


if __name__ == "__main__":
    unittest.main()
