#!/usr/bin/env python3
"""End-to-End tests for Lock-Free Parallel Rebase & Intelligent FIFO PR-to-Main Queue (Wave 1)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance_core.common import slugify_branch
from tools.pr_governance_core.lifecycle import cmd_merge
from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock
from tools.pr_governance_core.merge_queue import get_queue_status
from tools.pr_governance_core.sync import cmd_sync

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(k, None)
    return env


class TestPRSubmissionLeaseWave1E2E(unittest.TestCase):
    """Verifies parallel lock-free worktree rebase and FIFO queued PR-to-main execution."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_sub_lease_w1_")
        self.root = Path(self.tmp_dir).resolve()
        self.env = _git_clean_env()

        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Wave 1 Tester"])
        self._git(["config", "user.email", "wave1@example.com"])
        self._git(["config", "commit.gpgsign", "false"])

        (self.root / "README.md").write_text("# Base Repo\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-m", "chore: initial commit"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _git(self, args: list[str], cwd: Path | None = None) -> str:
        target = cwd or self.root
        res = subprocess.run(
            ["git"] + args,
            cwd=str(target),
            capture_output=True,
            text=True,
            check=True,
            env=self.env,
        )
        return res.stdout.strip()

    def _provision_worktree(self, branch: str, rel_file: str, content: str) -> Path:
        slug = slugify_branch(branch)
        wt_dir = self.root / ".worktrees" / slug
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "-b", branch, str(wt_dir), "main"])
        f_path = wt_dir / rel_file
        f_path.parent.mkdir(parents=True, exist_ok=True)
        f_path.write_text(content, encoding="utf-8")
        self._git(["add", rel_file], cwd=wt_dir)
        self._git(["commit", "-m", f"feat: add {rel_file} on {branch}"], cwd=wt_dir)
        return wt_dir

    def _seed_passing_receipts(self, branch: str, head_sha: str, base_sha: str, files: list[str]) -> None:
        slug = slugify_branch(branch)
        reports_dir = self.root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / f"pr_review_{slug}_receipt.json").write_text(
            json.dumps(
                {
                    "pr_type": "LOCAL_FEATURE_BRANCH_REVIEW",
                    "branch": branch,
                    "head_sha": head_sha,
                    "base": "main",
                    "base_commit_sha": base_sha,
                    "clean_merge": True,
                    "readiness_verified": True,
                    "wave_summary_verified": True,
                    "terminal_outcome": "PASS",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        r_dir = reports_dir / "readiness"
        r_dir.mkdir(parents=True, exist_ok=True)
        stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS", "dependencies": files} for i in range(1, 31)]
        (r_dir / f"local_main_readiness_{slug}_receipt.json").write_text(
            json.dumps(
                {
                    "branch": branch,
                    "head_commit_sha": head_sha,
                    "base_commit_sha": base_sha,
                    "terminal_outcome": "PASS",
                    "total_stages": 30,
                    "target_files": files,
                    "stages": stages,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_01_parallel_commit_and_rebase_without_queue_lock(self) -> None:
        """Verify two branches rebase onto local main simultaneously without acquiring the merge queue lock."""
        wt1 = self._provision_worktree("feat/branch-1", "mod_a.py", "A = 1\n")
        wt2 = self._provision_worktree("feat/branch-2", "mod_b.py", "B = 2\n")

        # Advance local main so both branches are 1 commit behind
        (self.root / "main_base.py").write_text("BASE = True\n", encoding="utf-8")
        self._git(["add", "main_base.py"])
        self._git(["commit", "-m", "chore: advance local main"])
        new_main_sha = self._git(["rev-parse", "main"])

        # Hold the merge queue lock on a dummy branch to prove parallel rebases do NOT block on it!
        with GovernedMergeQueueLock(branch="feat/other-merging-pr", repo_root=self.root, timeout_sec=10.0):
            results: dict[str, int] = {}

            def _run_sync(b_name: str) -> None:
                args = argparse.Namespace(
                    branch=b_name,
                    repo_root=self.root,
                    skip_readiness=True,
                    fail_fast=False,
                    full=False,
                    dry_run=False,
                    prune_caches=False,
                )
                results[b_name] = cmd_sync(args)

            t1 = threading.Thread(target=_run_sync, args=("feat/branch-1",))
            t2 = threading.Thread(target=_run_sync, args=("feat/branch-2",))
            t1.start()
            t2.start()
            t1.join(timeout=15.0)
            t2.join(timeout=15.0)

        self.assertEqual(results.get("feat/branch-1"), 0)
        self.assertEqual(results.get("feat/branch-2"), 0)
        self.assertEqual(self._git(["merge-base", "main", "feat/branch-1"], cwd=wt1), new_main_sha)
        self.assertEqual(self._git(["merge-base", "main", "feat/branch-2"], cwd=wt2), new_main_sha)

    def test_02_pre_queue_prerequisite_gate_rejects_before_lock(self) -> None:
        """Verify a branch missing PR_MERGE_AUTHORIZATION fails before entering the FIFO queue."""
        self._provision_worktree("feat/unratified", "unratified.py", "X = 0\n")
        ledger_file = self.root / ".local" / "ledger" / "hitl_decision_ledger.json"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text(json.dumps({"decisions": []}), encoding="utf-8")

        # Hold lock on another branch; feat/unratified must fail immediately without waiting on lock
        with (
            GovernedMergeQueueLock(branch="feat/active-winner", repo_root=self.root, timeout_sec=30.0),
            patch("tools.pr_governance_core.lifecycle.ROOT", self.root),
            patch("tools.pr_governance_core.common.ROOT", self.root),
        ):
            t0 = time.monotonic()
            args = argparse.Namespace(
                branch="feat/unratified",
                title="feat: unratified merge attempt",
                delete_branch=False,
                allow_open_waves=False,
            )
            rc = cmd_merge(args)
            elapsed = time.monotonic() - t0

            self.assertEqual(rc, 2, "Missing PR_MERGE_AUTHORIZATION must return exit code 2 before lock wait")
            self.assertLess(elapsed, 15.0, "Must fail before waiting on GovernedMergeQueueLock")
            q_status = get_queue_status(repo_root=self.root)
            self.assertEqual(len(q_status["pending_waiters"]), 0)

    def test_03_simultaneous_pr_to_main_fifo_pause_and_auto_catchup_e2e(self) -> None:
        """Verify Branch 1 wins PR-to-main while Branch 2 pauses in FIFO queue, wakes up, catch up, and merges."""
        wt1 = self._provision_worktree("feat/branch-1", "service_one.py", "ONE = 1\n")
        wt2 = self._provision_worktree("feat/branch-2", "service_two.py", "TWO = 2\n")

        initial_main = self._git(["rev-parse", "main"])
        sha1 = self._git(["rev-parse", "feat/branch-1"], cwd=wt1)
        sha2 = self._git(["rev-parse", "feat/branch-2"], cwd=wt2)

        self._seed_passing_receipts("feat/branch-1", sha1, initial_main, ["service_one.py"])
        self._seed_passing_receipts("feat/branch-2", sha2, initial_main, ["service_two.py"])

        from tools.pr_governance_core import lifecycle as lc_mod

        orig_execute = lc_mod._execute_merge_under_lock
        orig_sub_run = subprocess.run
        observed_waiters_during_b1: list[str] = []
        b1_entered_merge = threading.Event()

        def _instrumented_execute(branch: str, title: str, slug: str, repo_root: Path | None = None) -> int:
            if branch == "feat/branch-1":
                b1_entered_merge.set()
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    st = get_queue_status(repo_root=self.root)
                    waiter_branches = [w["branch"] for w in st["pending_waiters"]]
                    if "feat/branch-2" in waiter_branches:
                        observed_waiters_during_b1.extend(waiter_branches)
                        break
                    time.sleep(0.05)
            return orig_execute(branch, title, slug, repo_root=self.root)

        def _selective_run(cmd, *a, **kw):
            if isinstance(cmd, list) and any("verify_post_merge.py" in str(x) for x in cmd):
                m = MagicMock()
                m.returncode = 0
                m.stdout = "OK"
                m.stderr = ""
                return m
            return orig_sub_run(cmd, *a, **kw)

        merge_outcomes: dict[str, int] = {}

        with (
            patch("tools.pr_governance_core.lifecycle.ROOT", self.root),
            patch("tools.pr_governance_core.common.ROOT", self.root),
            patch("tools.pr_governance_core.lifecycle._execute_merge_under_lock", side_effect=_instrumented_execute),
            patch("tools.pr_governance_core.lifecycle.subprocess.run", side_effect=_selective_run),
            patch("tools.pr_governance_core.lifecycle._teardown_merged_worktree"),
            patch("tools.post_merge_report_regenerator.regenerate_all_reports"),
        ):
            def _run_merge(b_name: str, b_title: str) -> None:
                args = argparse.Namespace(
                    branch=b_name,
                    title=b_title,
                    delete_branch=False,
                    allow_open_waves=False,
                )
                merge_outcomes[b_name] = cmd_merge(args)

            t1 = threading.Thread(target=_run_merge, args=("feat/branch-1", "feat: service one"))
            t2 = threading.Thread(target=_run_merge, args=("feat/branch-2", "feat: service two"))

            t1.start()
            b1_entered_merge.wait(timeout=5.0)
            t2.start()

            t1.join(timeout=20.0)
            t2.join(timeout=20.0)

        self.assertEqual(merge_outcomes.get("feat/branch-1"), 0, "Branch 1 must win and merge cleanly")
        self.assertEqual(merge_outcomes.get("feat/branch-2"), 0, "Branch 2 must pause, wake up, catch up, and merge cleanly")
        self.assertIn("feat/branch-2", observed_waiters_during_b1, "Branch 2 must be recorded as paused in FIFO queue while Branch 1 merges")

        # Verify both merge commits landed on main in exact FIFO order (branch-1 first, branch-2 second)
        merges_log = self._git(["log", "main", "--merges", "-n", "2", "--format=%s"]).splitlines()
        self.assertEqual(len(merges_log), 2)
        self.assertIn("Merge branch 'feat/branch-2' into main", merges_log[0])
        self.assertIn("Merge branch 'feat/branch-1' into main", merges_log[1])

        # Verify queue is completely drained and unlocked after completion
        final_status = get_queue_status(repo_root=self.root)
        self.assertFalse(final_status["is_locked"])
        self.assertEqual(len(final_status["pending_waiters"]), 0)

        # Emit Wave 1 runtime receipt
        receipt_path = REPO_ROOT / "reports" / "wave1_pr_submission_lease_receipt.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "wave": 1,
                    "title": "Lock-Free Parallel Branch Rebase & Intelligent FIFO PR-to-Main Queue",
                    "status": "PASS",
                    "parallel_rebase_verified": True,
                    "fifo_pause_resume_verified": True,
                    "observed_fifo_waiters": observed_waiters_during_b1,
                    "merges_landed_in_order": list(reversed(merges_log)),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
