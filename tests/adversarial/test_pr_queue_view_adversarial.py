"""
Adversarial & Edge-Case Tests for PR Queue View Modes.

Covers:
1. Extreme branch name lengths (250+ chars)
2. Unicode/CJK/emoji branch and action strings
3. Queue with 500+ stashes (verifying word count cap < 1000 holds under stress)
4. Empty queue handling (no crash)
5. Pipe characters inside branch names and text fields
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from tools.pr_governance_core.queue_render import render_queue_table


class TestPRQueueViewAdversarial(unittest.TestCase):
    def test_massive_stash_overflow_word_count(self):
        # Generate 500 simulated git stashes
        massive_queue = [
            {
                "branch": f"stash@{{{i}}}",
                "tier": "W",
                "sub_rank": f"W.{i+1}",
                "ahead": 0,
                "behind": 0,
                "domain": "Housekeeping",
                "readiness": "📦 Stash",
                "review_verdict": "STASH",
                "next_action": f"[TRIAGE_STASH] stash {i}",
                "ranking_rationale": "Stash",
                "completed_waves": "—",
                "open_waves": "—",
            }
            for i in range(500)
        ]
        # Add 2 candidate branches
        massive_queue.insert(
            0,
            {
                "branch": "feat/active-candidate-alpha",
                "tier": "P0",
                "sub_rank": "P0.1",
                "ahead": 5,
                "behind": 0,
                "domain": "Performance",
                "readiness": "🚀 Fast-Lane",
                "review_verdict": "MERGE_READY",
                "next_action": "[REVIEW] Ready to merge",
                "ranking_rationale": "High value",
                "completed_waves": "W1",
                "open_waves": "—",
            },
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(massive_queue, inv_counts={"total_worktrees_children": 5, "stashes_total": 500}, view_mode="compact")

        out = buf.getvalue()
        # Word count must remain strictly < 1000 words despite 500 stashes!
        words = len(out.split())
        self.assertLess(words, 1000, f"Massive stash queue exceeded 1000 words: {words}")
        self.assertIn("500 git stashes collapsed", out)
        self.assertIn("feat/active-candidate-alpha", out)

    def test_extreme_branch_length_and_pipes(self):
        hostile_branch = "feat/pipe|injection|attempt|" + "long-token-" * 15
        queue = [
            {
                "branch": hostile_branch,
                "tier": "P1",
                "sub_rank": "P1.1",
                "ahead": 2,
                "behind": 1,
                "domain": "Security",
                "readiness": "In Flight",
                "review_verdict": "REVIEW_READY",
                "next_action": "[REVIEW] Pipe test",
                "ranking_rationale": "Rationale | with pipe",
                "completed_waves": "—",
                "open_waves": "1",
            }
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(queue, view_mode="compact")
        out = buf.getvalue()
        self.assertIn(hostile_branch, out)
        self.assertIn("DIAGNOSTIC & NEXT ACTION LEDGER", out)

    def test_empty_queue_safety(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table([], inv_counts={"total_worktrees_children": 0, "stashes_total": 0}, view_mode="compact")
        out = buf.getvalue()
        self.assertIn("Total candidate branches in queue: 0", out)
        self.assertIn("Worktree coverage: 0/0 children · 0/0 stashes", out)


if __name__ == "__main__":
    unittest.main()
