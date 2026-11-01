#!/usr/bin/env python3
"""End-to-End and Concurrency Tests for Starvation-Bounded Disjoint Queue Scheduling (Wave 2).

Verifies:
1. Ready disjoint waiter bypasses unverified/overlapping waiters up to MAX_BYPASS_COUNT (3).
2. Starvation bound: after 3 bypasses, head waiter is protected from further bypasses.
3. Overlapping waiter cannot bypass earlier waiters sharing modified files.
4. compute_topological_execution_sequence tie-breaks same-tier branches by minimum file overlap and singleton penalties.
5. process_queue invokes plan_merge_order to reorder pending batches before draining.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance_core.decision_graph import (
    SINGLETON_HOTSPOTS,
    _compute_contention_penalty,
    compute_topological_execution_sequence,
)
from tools.pr_governance_core.merge_queue import (
    MAX_BYPASS_COUNT,
    QueueItem,
    _read_waiters,
    _write_waiters,
    is_first_live_waiter,
    process_queue,
    register_fifo_waiter,
)


class MergeQueueDisjointSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mq_disjoint_")
        self.common_dir = Path(self.tmp_dir) / ".git"
        self.common_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_starvation_bounded_disjoint_bypass(self) -> None:
        """Vector 1: Ready disjoint waiter bypasses unverified head up to 3 times, then halts."""
        head_pid = 11111
        cand_pid = 22222

        with patch("tools.pr_governance_core.merge_queue._is_pid_alive", return_value=True):
            # Head waiter: unverified / overlapping
            register_fifo_waiter(
                self.common_dir,
                branch="feat/slow-overlapping",
                pid=head_pid,
                changed_files=["graph_healer.py"],
                has_valid_receipt=False,
            )

            # Candidate waiter: ready, 100% disjoint
            register_fifo_waiter(
                self.common_dir,
                branch="feat/fast-disjoint",
                pid=cand_pid,
                changed_files=["service_isolated.py"],
                has_valid_receipt=True,
            )

            # Bypass 1
            can_run_1 = is_first_live_waiter(
                self.common_dir,
                branch="feat/fast-disjoint",
                pid=cand_pid,
                changed_files=["service_isolated.py"],
                has_valid_receipt=True,
            )
            self.assertTrue(can_run_1)
            head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/slow-overlapping"][0]
            self.assertEqual(head.bypass_count, 1)

            # Bypass 2
            can_run_2 = is_first_live_waiter(
                self.common_dir,
                branch="feat/fast-disjoint",
                pid=cand_pid,
                changed_files=["service_isolated.py"],
                has_valid_receipt=True,
            )
            self.assertTrue(can_run_2)
            head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/slow-overlapping"][0]
            self.assertEqual(head.bypass_count, 2)

            # Bypass 3
            can_run_3 = is_first_live_waiter(
                self.common_dir,
                branch="feat/fast-disjoint",
                pid=cand_pid,
                changed_files=["service_isolated.py"],
                has_valid_receipt=True,
            )
            self.assertTrue(can_run_3)
            head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/slow-overlapping"][0]
            self.assertEqual(head.bypass_count, 3)

            # Bypass 4: Starvation bound reached! Head cannot be bypassed anymore
            can_run_4 = is_first_live_waiter(
                self.common_dir,
                branch="feat/fast-disjoint",
                pid=cand_pid,
                changed_files=["service_isolated.py"],
                has_valid_receipt=True,
            )
            self.assertFalse(can_run_4, "Candidate MUST NOT bypass head once bypass_count reaches MAX_BYPASS_COUNT (3)")
            head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/slow-overlapping"][0]
            self.assertEqual(head.bypass_count, 3, "Bypass count must remain capped at 3")

    def test_02_overlapping_waiter_cannot_bypass(self) -> None:
        """Vector 2: Ready waiter sharing modified files with head CANNOT bypass."""
        cur_pid = os.getpid()
        register_fifo_waiter(
            self.common_dir,
            branch="feat/first-branch",
            pid=cur_pid,
            changed_files=["common_module.py", "utils.py"],
            has_valid_receipt=False,
        )

        waiters = _read_waiters(self.common_dir)
        waiters.append(
            QueueItem(
                branch="feat/overlapping-candidate",
                title="Overlapping PR",
                enqueued_at="2026-10-20T00:00:00Z",
                enqueued_by=f"pid:{cur_pid}:cand",
                status="PENDING",
                changed_files=["common_module.py"],  # Overlaps!
                has_valid_receipt=True,
            )
        )
        _write_waiters(self.common_dir, waiters)

        with patch("tools.pr_governance_core.merge_queue._is_pid_alive", return_value=True):
            can_bypass = is_first_live_waiter(
                self.common_dir,
                branch="feat/overlapping-candidate",
                pid=cur_pid,
                changed_files=["common_module.py"],
                has_valid_receipt=True,
            )
            self.assertFalse(can_bypass, "Overlapping waiter MUST NOT bypass head waiter")
            head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/first-branch"][0]
            self.assertEqual(head.bypass_count, 0, "Bypass count must remain 0 when bypass rejected")

    def test_03_topological_execution_sequence_overlap_tie_break(self) -> None:
        """Vector 3: Decision graph schedules disjoint branches ahead of overlapping/singleton cohorts within tier."""
        eligible_branches = [
            {
                "branch": "feat/singleton-heavy",
                "tier": "P1",
                "changed_files": ["implementation_plan.md", "evaluator.py"],
            },
            {
                "branch": "feat/overlap-shared",
                "tier": "P1",
                "changed_files": ["evaluator.py"],
            },
            {
                "branch": "feat/completely-disjoint",
                "tier": "P1",
                "changed_files": ["isolated_feature.py"],
            },
        ]
        hard_graph: dict[str, list[str]] = {}
        cycles: list[list[str]] = []

        seq, blocked = compute_topological_execution_sequence(eligible_branches, hard_graph, cycles)
        self.assertEqual(blocked, {})
        # Completely disjoint branch must be scheduled FIRST among same-tier branches
        self.assertEqual(
            seq[0],
            "feat/completely-disjoint",
            "Disjoint branch must be scheduled ahead of overlapping and singleton-touching cohorts",
        )
        # Singleton-heavy branch must have the highest penalty and run last
        self.assertEqual(
            seq[-1],
            "feat/singleton-heavy",
            "Singleton-heavy branch must run after disjoint and low-contention branches",
        )

    def test_04_process_queue_invokes_plan_merge_order(self) -> None:
        """Vector 4: process_queue invokes plan_merge_order to reorder pending batch before draining."""
        waiters = [
            QueueItem(branch="feat/pr-b", title="PR B", enqueued_at="2026-10-20T00:00:00Z", status="PENDING"),
            QueueItem(branch="feat/pr-a", title="PR A", enqueued_at="2026-10-20T00:01:00Z", status="PENDING"),
        ]
        _write_waiters(self.common_dir, waiters)

        # Mock MergeOrderPlan with ordered_steps reversing PR B and PR A
        from tools.pr_overlap_core.order_planner import MergeOrderPlan, MergeStep

        mock_plan = MergeOrderPlan(
            branches=["feat/pr-a", "feat/pr-b"],
            ordered_steps=[
                MergeStep(step_index=0, branch="feat/pr-a", action="MERGE"),
                MergeStep(step_index=1, branch="feat/pr-b", action="MERGE"),
            ],
        )

        land_calls: list[str] = []

        def mock_land(branch: str, title: str, repo_root: Path, timeout_sec: float):
            land_calls.append(branch)
            mock_res = MagicMock()
            mock_res.status = "SUCCESS"
            mock_res.error_message = ""
            mock_res.to_dict.return_value = {"branch": branch, "status": "SUCCESS"}
            return mock_res

        with (
            patch("tools.pr_overlap_core.order_planner.plan_merge_order", return_value=mock_plan) as pmo,
            patch("tools.rebase_orchestrator.land_branch_serialized", side_effect=mock_land),
            patch("tools.pr_governance_core.merge_queue.resolve_git_common_dir", return_value=self.common_dir),
        ):
            results = process_queue(repo_root=Path(self.tmp_dir))

        self.assertEqual(len(results), 2)
        pmo.assert_called_once()
        # Verify execution order matched the optimized plan (feat/pr-a first, then feat/pr-b)
        self.assertEqual(land_calls, ["feat/pr-a", "feat/pr-b"])


if __name__ == "__main__":
    unittest.main()
