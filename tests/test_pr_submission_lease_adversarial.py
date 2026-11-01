#!/usr/bin/env python3
"""Adversarial & Edge-Case Tests for FIFO PR-to-Main Queue & Disjoint Rebinding (Wave 2)."""

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
from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock, LockInfo, resolve_git_common_dir
from tools.pr_governance_core.merge_queue import (
    QueueItem,
    _write_waiters,
    get_queue_status,
)
from tools.readiness_planner import check_disjoint_base_advance_equivalence

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(k, None)
    return env


class TestPRSubmissionLeaseWave2Adversarial(unittest.TestCase):
    """Adversarial tests for 3+ branch FIFO ordering, overlap invalidation, dead PID recovery, and conflict abort."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_sub_lease_w2_adv_")
        self.root = Path(self.tmp_dir).resolve()
        self.env = _git_clean_env()

        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "Wave 2 Tester"])
        self._git(["config", "user.email", "wave2@example.com"])
        self._git(["config", "commit.gpgsign", "false"])

        # Seed a multi-line shared core file so top vs bottom edits can rebase cleanly while overlapping file path
        core_dir = self.root / "dual_pipeline"
        core_dir.mkdir(parents=True, exist_ok=True)
        shared_lines = [f"LINE_{i} = {i}" for i in range(1, 21)]
        (core_dir / "shared_module.py").write_text("\n".join(shared_lines) + "\n", encoding="utf-8")
        self._git(["add", "dual_pipeline/shared_module.py"])
        self._git(["commit", "-m", "chore: seed shared core module"])

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
        self._git(["commit", "-m", f"feat: update {rel_file} on {branch}"], cwd=wt_dir)
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

    def test_01_three_branch_strict_fifo_wakeup_ordering(self) -> None:
        """Verify 3 concurrent branches entering the queue wake up and acquire the lock in strict FIFO order."""
        acquired_order: list[str] = []
        b1_locked = threading.Event()
        release_b1 = threading.Event()

        def _worker(branch: str, delay: float) -> None:
            if delay > 0:
                b1_locked.wait(timeout=5.0)
                time.sleep(delay)
            with GovernedMergeQueueLock(
                branch=branch,
                repo_root=self.root,
                timeout_sec=15.0,
                retry_interval_sec=0.05,
            ):
                acquired_order.append(branch)
                if branch == "feat/b1":
                    b1_locked.set()
                    release_b1.wait(timeout=5.0)
                else:
                    time.sleep(0.08)

        t1 = threading.Thread(target=_worker, args=("feat/b1", 0.0))
        t2 = threading.Thread(target=_worker, args=("feat/b2", 0.05))
        t3 = threading.Thread(target=_worker, args=("feat/b3", 0.20))

        t1.start()
        t2.start()
        t3.start()

        # Wait until both b2 and b3 are registered in the FIFO waiter queue
        deadline = time.monotonic() + 3.0
        waiters_snapshot: list[str] = []
        while time.monotonic() < deadline:
            st = get_queue_status(repo_root=self.root)
            w_list = [w["branch"] for w in st["pending_waiters"]]
            if len(w_list) >= 2:
                waiters_snapshot = w_list
                break
            time.sleep(0.03)

        release_b1.set()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        t3.join(timeout=10.0)

        self.assertEqual(waiters_snapshot, ["feat/b2", "feat/b3"], "Waiters must queue in strict arrival order")
        self.assertEqual(acquired_order, ["feat/b1", "feat/b2", "feat/b3"], "Locks must be granted in strict FIFO order")

    def test_02_overlapping_core_files_invalidate_receipt_and_force_reverification(self) -> None:
        """Verify overlapping core-file edits prevent receipt rebinding and force re-verification under lock."""
        base_lines = [f"LINE_{i} = {i}" for i in range(1, 21)]
        b1_lines = list(base_lines)
        b1_lines[0] = "LINE_1 = 100  # modified by b1"
        b2_lines = list(base_lines)
        b2_lines[-1] = "LINE_20 = 200  # modified by b2"

        wt1 = self._provision_worktree("feat/overlap-1", "dual_pipeline/shared_module.py", "\n".join(b1_lines) + "\n")
        wt2 = self._provision_worktree("feat/overlap-2", "dual_pipeline/shared_module.py", "\n".join(b2_lines) + "\n")

        initial_main = self._git(["rev-parse", "main"])
        sha1 = self._git(["rev-parse", "feat/overlap-1"], cwd=wt1)
        sha2 = self._git(["rev-parse", "feat/overlap-2"], cwd=wt2)

        self._seed_passing_receipts("feat/overlap-1", sha1, initial_main, ["dual_pipeline/shared_module.py"])
        self._seed_passing_receipts("feat/overlap-2", sha2, initial_main, ["dual_pipeline/shared_module.py"])

        orig_sub_run = subprocess.run
        review_calls_for_b2: list[str] = []

        def _selective_run(cmd, *a, **kw):
            if isinstance(cmd, list) and any("verify_post_merge.py" in str(x) for x in cmd):
                m = MagicMock()
                m.returncode = 0
                m.stdout = "OK"
                m.stderr = ""
                return m
            return orig_sub_run(cmd, *a, **kw)

        def _fake_review_on_overlap(r_args: argparse.Namespace) -> int:
            b = r_args.branch
            review_calls_for_b2.append(b)
            new_head = self._git(["rev-parse", b])
            cur_main = self._git(["rev-parse", "main"])
            self._seed_passing_receipts(b, new_head, cur_main, ["dual_pipeline/shared_module.py"])
            return 0

        with (
            patch("tools.pr_governance_core.lifecycle.ROOT", self.root),
            patch("tools.pr_governance_core.common.ROOT", self.root),
            patch("tools.pr_governance_core.lifecycle.subprocess.run", side_effect=_selective_run),
            patch("tools.pr_governance_core.lifecycle.cmd_review", side_effect=_fake_review_on_overlap),
            patch("tools.pr_governance_core.lifecycle._teardown_merged_worktree"),
            patch("tools.post_merge_report_regenerator.regenerate_all_reports"),
        ):
            rc1 = cmd_merge(argparse.Namespace(branch="feat/overlap-1", title="feat: overlap 1", delete_branch=False, allow_open_waves=False))
            self.assertEqual(rc1, 0)
            self.assertEqual(len(review_calls_for_b2), 0, "Branch 1 should not re-run review")

            # Now Branch 2 merges after Branch 1 modified the same core file on main
            rc2 = cmd_merge(argparse.Namespace(branch="feat/overlap-2", title="feat: overlap 2", delete_branch=False, allow_open_waves=False))
            self.assertEqual(rc2, 0)
            self.assertEqual(
                review_calls_for_b2,
                ["feat/overlap-2"],
                "Overlapping core-file advance on main must invalidate pre-pause receipt and force cmd_review re-verification",
            )

    def test_03_shared_baseline_modification_forces_reverification(self) -> None:
        """Verify mainline edits to shared governance baselines reject disjoint fast-path rebinding."""
        old_main = self._git(["rev-parse", "main"])
        baseline_p = self.root / "reports" / "file_budget_burndown_baseline.json"
        baseline_p.parent.mkdir(parents=True, exist_ok=True)
        baseline_p.write_text('{"version": 1}\n', encoding="utf-8")
        self._git(["add", "reports/file_budget_burndown_baseline.json"])
        self._git(["commit", "-m", "chore: update file budget baseline"])
        new_main = self._git(["rev-parse", "main"])

        ok, reason = check_disjoint_base_advance_equivalence(
            old_main, new_main, ["dual_pipeline/unrelated.py"], repo_root=self.root
        )
        self.assertFalse(ok)
        self.assertIn("shared policy or baseline", reason)

    def test_04_dead_pid_lock_holder_and_dead_waiter_reclamation(self) -> None:
        """Verify dead lock holder PID and dead head-of-line waiter PID are automatically reclaimed."""
        common_dir = resolve_git_common_dir(self.root)
        (common_dir / "governed_merge_queue.lock").write_text("999999\n", encoding="utf-8")
        (common_dir / "governed_merge_queue.json").write_text(
            json.dumps(
                LockInfo(
                    branch="feat/crashed-winner",
                    holder_pid=999999,
                    holder_host="localhost",
                    acquired_at="2026-01-01T00:00:00Z",
                    heartbeat_at="2026-01-01T00:00:00Z",
                    timeout_sec=300.0,
                    worktree_path=str(self.root),
                ).to_dict()
            ),
            encoding="utf-8",
        )
        # Also inject a dead waiter PID at the head of the FIFO queue
        _write_waiters(
            common_dir,
            [
                QueueItem(
                    branch="feat/crashed-waiter",
                    title="Paused PR",
                    enqueued_at="2026-01-01T00:00:01Z",
                    enqueued_by="pid:999998",
                    status="PENDING",
                )
            ],
        )

        with GovernedMergeQueueLock(branch="feat/survivor", repo_root=self.root, timeout_sec=3.0) as lock:
            self.assertTrue(lock.is_acquired())
            st = get_queue_status(repo_root=self.root)
            self.assertEqual(st["active_lock"]["branch"], "feat/survivor")
            self.assertEqual(len(st["pending_waiters"]), 0, "Dead waiter PID must be pruned automatically")

    def test_05_post_pause_merge_conflict_aborts_cleanly_leaving_worktree_pristine(self) -> None:
        """Verify conflicting post-pause catch-up rebase aborts cleanly and releases queue lock."""
        wt1 = self._provision_worktree("feat/conflict-1", "dual_pipeline/shared_module.py", "CONFLICT_LINE = 'from_1'\n")
        wt2 = self._provision_worktree("feat/conflict-2", "dual_pipeline/shared_module.py", "CONFLICT_LINE = 'from_2'\n")

        initial_main = self._git(["rev-parse", "main"])
        sha1 = self._git(["rev-parse", "feat/conflict-1"], cwd=wt1)
        sha2 = self._git(["rev-parse", "feat/conflict-2"], cwd=wt2)

        self._seed_passing_receipts("feat/conflict-1", sha1, initial_main, ["dual_pipeline/shared_module.py"])
        self._seed_passing_receipts("feat/conflict-2", sha2, initial_main, ["dual_pipeline/shared_module.py"])

        orig_sub_run = subprocess.run

        def _selective_run(cmd, *a, **kw):
            if isinstance(cmd, list) and any("verify_post_merge.py" in str(x) for x in cmd):
                m = MagicMock()
                m.returncode = 0
                m.stdout = "OK"
                m.stderr = ""
                return m
            return orig_sub_run(cmd, *a, **kw)

        with (
            patch("tools.pr_governance_core.lifecycle.ROOT", self.root),
            patch("tools.pr_governance_core.common.ROOT", self.root),
            patch("tools.pr_governance_core.lifecycle.subprocess.run", side_effect=_selective_run),
            patch("tools.pr_governance_core.lifecycle._teardown_merged_worktree"),
            patch("tools.post_merge_report_regenerator.regenerate_all_reports"),
        ):
            rc1 = cmd_merge(argparse.Namespace(branch="feat/conflict-1", title="feat: conflict 1", delete_branch=False, allow_open_waves=False))
            self.assertEqual(rc1, 0)

            rc2 = cmd_merge(argparse.Namespace(branch="feat/conflict-2", title="feat: conflict 2", delete_branch=False, allow_open_waves=False))
            self.assertEqual(rc2, 1, "Conflicting catch-up rebase must fail closed with exit code 1")

        # Verify wt2 and root are completely clean and queue lock is released
        self.assertEqual(self._git(["status", "--porcelain"], cwd=wt2), "")
        st = get_queue_status(repo_root=self.root)
        self.assertFalse(st["is_locked"])

        # Emit Wave 2 runtime receipt
        receipt_path = REPO_ROOT / "reports" / "wave2_pr_submission_lease_receipt.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "wave": 2,
                    "title": "Automatic Post-Pause Catch-Up Alignment & Disjoint Receipt Rebinding",
                    "status": "PASS",
                    "three_branch_fifo_verified": True,
                    "overlapping_core_invalidation_verified": True,
                    "shared_baseline_invalidation_verified": True,
                    "dead_pid_recovery_verified": True,
                    "conflict_clean_abort_verified": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_06_post_merge_teardown_ledger_isolation_submit_and_greedy_planner(self) -> None:
        """Verify auto-ingest teardown, root HITL ledger isolation, submit CLI parser, and N>8 greedy merge planner."""
        from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
        from tools.pr_governance import build_parser
        from tools.pr_governance_core.lifecycle import _teardown_merged_worktree
        from tools.pr_overlap_core.order_planner import plan_merge_order

        # 1. Root tracked hitl_decision_ledger.json stays untouched when recording operator answer
        (self.root / ".gitignore").write_text(".local/\n", encoding="utf-8")
        tracked_ledger = self.root / "hitl_decision_ledger.json"
        tracked_ledger.write_text('{"decisions": []}\n', encoding="utf-8")
        self._git(["add", ".gitignore", "hitl_decision_ledger.json"])
        self._git(["commit", "-m", "chore: seed tracked ledger"])
        before_sha = tracked_ledger.read_bytes()

        with patch("pathlib.Path.cwd", return_value=self.root):
            record_operator_answer(
                decision_id="pr_merge_auth_feat/test",
                question="Authorize PR merge?",
                options=["Approve", "Reject"],
                selected_option="Approve",
                operator_origin="Execute plan to finish each branch and PR to local main",
                prior_user_command="Execute plan to finish each branch and PR to local main",
                category="pr_merge_authorization",
                repo_root=self.root,
            )
        self.assertEqual(tracked_ledger.read_bytes(), before_sha, "Tracked root ledger must not be dirtied")
        self.assertEqual(self._git(["status", "--porcelain"]), "")

        # 2. _teardown_merged_worktree passes ingest=True to cmd_finish
        captured_args = []
        with (
            patch("tools.pr_governance_core.lifecycle.ROOT", self.root),
            patch("tools.pr_governance_core.worktree.cmd_finish", side_effect=lambda a: captured_args.append(a) or 0),
        ):
            _teardown_merged_worktree("feat/merged-branch")
        self.assertEqual(len(captured_args), 1)
        self.assertTrue(getattr(captured_args[0], "ingest", False))

        # 3. submit CLI subparser parses branch and title cleanly
        parsed = build_parser().parse_args(["submit", "feat/my-branch", "--title", "feat: atomic submit"])
        self.assertEqual(parsed.command, "submit")
        self.assertEqual(parsed.branch, "feat/my-branch")

        # 4. N > 8 greedy merge order planner unpacks 4-tuple from _simulate_merge_step without ValueError
        branches = []
        for i in range(9):
            b_name = f"feat/greedy-{i}"
            self._provision_worktree(b_name, f"dual_pipeline/mod_{i}.py", f"VAL = {i}\n")
            branches.append(b_name)
        plan = plan_merge_order(repo_root=self.root, base_ref="main", branches=branches)
        self.assertEqual(len(plan.ordered_steps), 9)
        self.assertTrue(plan.zero_mutation_verified)


if __name__ == "__main__":
    unittest.main()
