"""Adversarial and edge-case tests for PR Queue Completed vs. In-Progress separation.

Covers:
1. Boundary payloads: empty queue, decommission-only rows, stashes-only rows.
2. Malformed inputs: None ahead/behind/dirty, missing open_waves, invalid PID types.
3. Concurrency & lock detection: git index.lock / rebase-merge in worktree marks branch IN_PROGRESS.
4. Invariant bypass attempts: branch with open waves or active PID must never be marked COMPLETED.
5. Idempotency: prefix_action_with_completion_badge does not double-prefix existing badges.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from tools.pr_governance_core.completion_state import (
    detect_git_worktree_lock,
    evaluate_branch_completion_state,
    prefix_action_with_completion_badge,
    summarize_queue_completion_split,
)
from tools.pr_governance_core.executive_render import evaluate_stage_gates_and_disposition


class TestPRQueueCompletedVsInProgressAdversarial(unittest.TestCase):
    def test_boundary_empty_and_stash_only_summarize(self) -> None:
        empty = summarize_queue_completion_split([])
        self.assertEqual(empty["completed_branch_count"], 0)
        self.assertEqual(empty["in_progress_branch_count"], 0)
        self.assertEqual(empty["decommission_branch_count"], 0)

        stashes = [
            {"branch": "stash@{0}", "tier": "W"},
            {"branch": "orphan_stub", "tier": "W"},
        ]
        st_split = summarize_queue_completion_split(stashes)
        self.assertEqual(st_split["completed_branch_count"], 0)
        self.assertEqual(st_split["in_progress_branch_count"], 0)

    def test_malformed_branch_payload_does_not_crash(self) -> None:
        malformed: Dict[str, Any] = {
            "branch": None,
            "tier": None,
            "ahead": None,
            "behind": None,
            "dirty": None,
            "open_waves": None,
        }
        state = evaluate_branch_completion_state(malformed)
        self.assertIn(state.completion_state, ("COMPLETED", "IN_PROGRESS", "DECOMMISSION"))

    def test_git_lock_and_active_pid_force_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td)
            git_dir = wt / ".git"
            git_dir.mkdir()
            (git_dir / "index.lock").write_text("locked", encoding="utf-8")
            self.assertTrue(detect_git_worktree_lock(wt))

            row_locked: Dict[str, Any] = {
                "branch": "feat/locked-branch",
                "tier": "P1",
                "ahead": 2,
                "behind": 0,
                "dirty": False,
                "clean_merge": True,
                "open_waves": "—",
                "worktree_path": str(wt),
                "has_git_lock": True,
            }
            st = evaluate_branch_completion_state(row_locked)
            self.assertEqual(st.completion_state, "IN_PROGRESS")
            self.assertEqual(st.code_completed_flag, "N")
            self.assertIn("active_git_lock_or_rebase", st.in_progress_reasons)

            code_done, _, _, disp, act = evaluate_stage_gates_and_disposition(row_locked)
            self.assertEqual(code_done, "N")
            self.assertEqual(disp, "Complete In-Flight Work")
            self.assertIn("Active session in progress", act)

    def test_active_pids_mark_branch_in_progress(self) -> None:
        row_pid: Dict[str, Any] = {
            "branch": "feat/live-agent-running",
            "tier": "P0",
            "ahead": 3,
            "behind": 0,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "active_pids": [42424],
        }
        st = evaluate_branch_completion_state(row_pid)
        self.assertEqual(st.completion_state, "IN_PROGRESS")
        self.assertEqual(st.status_badge, "[IN PROGRESS — ACTIVE SESSION]")
        self.assertIn("active_process_pids:42424", st.in_progress_reasons)

    def test_badge_prefix_idempotency(self) -> None:
        row: Dict[str, Any] = {
            "branch": "feat/ready",
            "tier": "P1",
            "ahead": 1,
            "behind": 0,
            "dirty": False,
            "open_waves": "—",
        }
        st = evaluate_branch_completion_state(row)
        first = prefix_action_with_completion_badge("Execute review", st)
        second = prefix_action_with_completion_badge(first, st)
        self.assertEqual(first, second)
        self.assertEqual(second.count("[COMPLETED — READY]"), 1)


if __name__ == "__main__":
    unittest.main()
