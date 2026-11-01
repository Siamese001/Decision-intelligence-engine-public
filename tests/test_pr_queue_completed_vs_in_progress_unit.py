"""Unit tests for PR Queue Completed vs. In-Progress branch separation."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from tools.pr_governance_core.decision_api import evaluate_queue_decisions
from tools.pr_governance_core.detail_artifact import generate_pr_queue_detail_artifact
from tools.pr_governance_core.executive_render import (
    evaluate_stage_gates_and_disposition,
    render_executive_table,
)
from tools.pr_governance_core.queue_observer_table import render_observer_table
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue
from tools.pr_governance_core.queue_snapshot import BranchSnapshot, QueueSnapshot


class TestPRQueueCompletedVsInProgressUnit(unittest.TestCase):
    def test_completed_vs_in_progress_classification_and_badges(self) -> None:
        from tools.pr_governance_core.completion_state import (
            evaluate_branch_completion_state,
            prefix_action_with_completion_badge,
            summarize_queue_completion_split,
        )

        completed_row: Dict[str, Any] = {
            "branch": "feat/completed-feature",
            "tier": "P1",
            "ahead": 3,
            "behind": 2,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "completed_waves": "1, 2",
        }
        c_state = evaluate_branch_completion_state(completed_row)
        self.assertEqual(c_state.completion_state, "COMPLETED")
        self.assertTrue(c_state.is_completed)
        self.assertFalse(c_state.is_in_progress)
        self.assertEqual(c_state.code_completed_flag, "Y")
        self.assertEqual(c_state.completion_rank, 0)
        self.assertTrue(
            prefix_action_with_completion_badge("Rebase onto main", c_state).startswith("[COMPLETED — READY]")
        )

        dirty_row: Dict[str, Any] = {
            "branch": "feat/dirty-in-progress",
            "tier": "P1",
            "ahead": 2,
            "behind": 0,
            "dirty": True,
            "dirty_files_count": 3,
            "clean_merge": True,
            "open_waves": "—",
        }
        d_state = evaluate_branch_completion_state(dirty_row)
        self.assertEqual(d_state.completion_state, "IN_PROGRESS")
        self.assertFalse(d_state.is_completed)
        self.assertTrue(d_state.is_in_progress)
        self.assertEqual(d_state.code_completed_flag, "N")
        self.assertEqual(d_state.completion_rank, 1)
        self.assertIn("uncommitted_changes", d_state.in_progress_reasons)
        self.assertTrue(
            prefix_action_with_completion_badge("Commit 3 authored file(s)", d_state).startswith("[IN PROGRESS —")
        )

        open_wave_row: Dict[str, Any] = {
            "branch": "feat/waves-in-progress",
            "tier": "P1",
            "ahead": 1,
            "behind": 0,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "2, 3",
        }
        w_state = evaluate_branch_completion_state(open_wave_row)
        self.assertEqual(w_state.completion_state, "IN_PROGRESS")
        self.assertEqual(w_state.code_completed_flag, "N")
        self.assertIn("open_waves:2, 3", w_state.in_progress_reasons)

        summary = summarize_queue_completion_split([completed_row, dirty_row, open_wave_row])
        self.assertEqual(summary["completed_branch_count"], 1)
        self.assertEqual(summary["in_progress_branch_count"], 2)
        self.assertEqual(summary["completed_branches"], ["feat/completed-feature"])
        self.assertEqual(
            summary["in_progress_branches"],
            ["feat/dirty-in-progress", "feat/waves-in-progress"],
        )

    def test_completed_branches_sort_ahead_of_in_progress_within_same_tier(self) -> None:
        rows: List[Dict[str, Any]] = [
            {
                "branch": "feat/a-in-progress-waves",
                "tier": "P1",
                "benefit_category": "FEATURE_AND_DELIVERABLE",
                "ahead": 2,
                "behind": 0,
                "dirty": False,
                "clean_merge": True,
                "open_waves": "2",
                "risk_tier": 0,
                "value_score": 50.0,
            },
            {
                "branch": "feat/z-completed-ready",
                "tier": "P1",
                "benefit_category": "FEATURE_AND_DELIVERABLE",
                "ahead": 2,
                "behind": 0,
                "dirty": False,
                "clean_merge": True,
                "open_waves": "—",
                "risk_tier": 0,
                "value_score": 50.0,
            },
        ]
        assign_sub_ranks(sort_queue(rows))
        self.assertEqual(rows[0]["branch"], "feat/z-completed-ready")
        self.assertEqual(rows[1]["branch"], "feat/a-in-progress-waves")
        self.assertIn("completed", rows[1]["ranking_rationale"].lower())

    def test_executive_and_observer_tables_and_detail_artifact_separate_completed_vs_in_progress(self) -> None:
        queue: List[Dict[str, Any]] = [
            {
                "tier": "P1",
                "sub_rank": "P1.1",
                "branch": "feat/completed-branch",
                "domain": "Governance",
                "ahead": 2,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "open_waves": "—",
            },
            {
                "tier": "P1",
                "sub_rank": "P1.2",
                "branch": "feat/in-progress-branch",
                "domain": "Governance",
                "ahead": 1,
                "behind": 0,
                "clean_merge": True,
                "dirty": True,
                "dirty_files_count": 2,
                "open_waves": "Wave 2",
            },
        ]

        obs_md = render_observer_table(queue, {"mode": "interactive", "outcome": "COMPLETE"})
        self.assertIn("[COMPLETED — READY]", obs_md)
        self.assertIn("[IN PROGRESS —", obs_md)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            detail_path = root / "reports" / "pr_queue_detail.json"
            buf = io.StringIO()
            with redirect_stdout(buf):
                render_executive_table(
                    queue,
                    inv_counts={"total_children": 2, "covered_children": 2, "total_stashes": 0, "covered_stashes": 0},
                    detail_path=detail_path,
                )
            exec_out = buf.getvalue()
            self.assertIn("Completed & ready branches: 1 · In-progress branches: 1", exec_out)
            self.assertIn("[COMPLETED — READY]", exec_out)
            self.assertIn("[IN PROGRESS —", exec_out)

            generate_pr_queue_detail_artifact(root, queue, target_path=detail_path)
            payload = json.loads(detail_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["completed_branch_count"], 1)
            self.assertEqual(payload["in_progress_branch_count"], 1)
            self.assertEqual(payload["completed_branches"], ["feat/completed-branch"])
            self.assertEqual(payload["in_progress_branches"], ["feat/in-progress-branch"])
            b_map = {b["branch"]: b for b in payload["branches"]}
            self.assertEqual(b_map["feat/completed-branch"]["completion_state"], "COMPLETED")
            self.assertEqual(b_map["feat/in-progress-branch"]["completion_state"], "IN_PROGRESS")
            self.assertTrue(len(b_map["feat/in-progress-branch"]["in_progress_reasons"]) >= 1)


if __name__ == "__main__":
    unittest.main()
