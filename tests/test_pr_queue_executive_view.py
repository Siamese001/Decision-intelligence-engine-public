"""
Unit test suite for the 5-column Executive PR Queue view.

Verifies:
1. _format_executive_disposition logic across all 7 state categories.
2. 5-column executive table structure and header format.
3. Proper filtering of active candidate branches vs. collapsed stashes/stubs.
4. Retention of mandatory coverage metrics under INV-WT-COVERAGE.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict

from tools.pr_governance_core.render import (
    _format_executive_disposition,
    _render_executive_table,
)


class TestPRQueueExecutiveView(unittest.TestCase):
    def test_format_executive_disposition_states(self) -> None:
        # 1. Decommission
        row_prune = {"tier": "D", "next_action": "[PRUNE] abandoned", "ranking_rationale": "zero commits"}
        disp, action = _format_executive_disposition(row_prune)
        self.assertEqual(disp, "Decommission (No Value)")
        self.assertIn("Safely prune", action)

        # 2. Inception work (ahead == 0 and dirty)
        row_dirty = {"ahead": 0, "behind": 10, "dirty": True, "clean_merge": True}
        disp, action = _format_executive_disposition(row_dirty)
        self.assertEqual(disp, "Complete In-Flight Work")
        self.assertIn("Commit authored inception work", action)

        # 3. Merge conflicts
        row_conflict = {"ahead": 1, "behind": 5, "clean_merge": False, "rebase_effort": "MEDIUM"}
        disp, action = _format_executive_disposition(row_conflict)
        self.assertEqual(disp, "Promote to Main")
        self.assertIn("resolve conflicting file", action)

        # 4. Governance block
        row_gov = {"ahead": 1, "behind": 0, "clean_merge": True, "review_verdict": "GOVERNANCE_BLOCK"}
        disp, action = _format_executive_disposition(row_gov)
        self.assertEqual(disp, "Hold / Blocked")
        self.assertIn("Resolve failing governance gate", action)

        # 5. Open waves
        row_waves = {"ahead": 1, "behind": 0, "clean_merge": True, "open_waves": "2, 3"}
        disp, action = _format_executive_disposition(row_waves)
        self.assertEqual(disp, "Complete In-Flight Work")
        self.assertIn("Complete open wave(s) [2, 3]", action)

        # 6. Diverged clean merge (behind > 0 strictly requires rebase)
        row_diverged = {"ahead": 1, "behind": 15, "clean_merge": True, "dirty": False}
        disp, action = _format_executive_disposition(row_diverged)
        self.assertEqual(disp, "Promote to Main")
        self.assertIn("15 commits behind", action)

        # 7. Ready Now
        row_ready = {"ahead": 2, "behind": 0, "clean_merge": True, "dirty": False, "branch": "feat/my-feature"}
        disp, action = _format_executive_disposition(row_ready)
        self.assertEqual(disp, "Promote to Main")
        self.assertIn("Stage 3 Ready", action)

    def test_render_executive_table_structure_and_collapse(self) -> None:
        queue: list[Dict[str, Any]] = [
            {
                "tier": "P1",
                "branch": "feat/high-impact",
                "domain": "Performance",
                "ahead": 2,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "open_waves": "—",
            },
            {
                "tier": "P2",
                "branch": "feat/in-flight-work",
                "domain": "Ingestion",
                "ahead": 1,
                "behind": 5,
                "clean_merge": False,
                "rebase_effort": "LOW",
                "dirty": False,
                "open_waves": "—",
            },
            {
                "tier": "W",
                "branch": "stash@{0}",
                "domain": "Housekeeping",
            },
            {
                "tier": "W",
                "branch": "stash@{1}",
                "domain": "Housekeeping",
            },
            {
                "tier": "W",
                "branch": "02e0",
                "domain": "Housekeeping",
            },
            {
                "tier": "D",
                "branch": "feat/abandoned",
                "domain": "Housekeeping",
                "ahead": 0,
                "behind": 50,
                "next_action": "[PRUNE] stale branch",
                "ranking_rationale": "zero commits",
            },
        ]

        inv_counts = {"total_children": 3, "covered_children": 3, "total_stashes": 2, "covered_stashes": 2}
        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_executive_table(queue, inv_counts=inv_counts, detail_path=Path("reports/pr_queue_detail.json"))

        output = buf.getvalue()

        # Check 10-column header
        self.assertIn("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |", output)

        # Active branches rendered
        self.assertIn("feat/high-impact", output)
        self.assertIn("feat/in-flight-work", output)
        self.assertIn("feat/abandoned", output)

        # Priority numbering
        self.assertIn("| **1** |", output)
        self.assertIn("| **2** |", output)
        self.assertIn("| **3** |", output)

        # Collapsed row for stashes and stubs
        self.assertIn("1 session stub(s) & 2 git stash(es) collapsed", output)

        # Footers
        self.assertIn("Active candidate branches: 3", output)
        self.assertIn("Total entries tracked: 6", output)
        self.assertIn("Worktree coverage: 3/3 children · 2/2 stashes", output)


if __name__ == "__main__":
    unittest.main()
