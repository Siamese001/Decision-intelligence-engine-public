"""tests/test_pr_queue_executive_complexity_view.py

Unit tests for the 6-column executive disposition table rendering engine
incorporating refactoring complexity T-shirt sizing (XS, S, M, L, XL) and
5-Agent Council deliberation display.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from typing import Any, Dict, List

from tools.pr_governance_core.executive_render import render_executive_table
from tools.pr_governance_core.render import render_queue_table


class TestPRQueueExecutiveComplexityView(unittest.TestCase):
    def setUp(self):
        self.sample_queue: List[Dict[str, Any]] = [
            {
                "branch": "feat/first-ready",
                "tier": "P0",
                "domain": "Core Engine",
                "ahead": 2,
                "behind": 0,
                "churn": 80,
                "file_count": 2,
                "clean_merge": True,
                "dirty": False,
                "open_waves": "—",
                "completed_waves": "1-3",
                "next_action": "Run Class B receipt and merge to main",
                "changed_files": ["tools/core.py", "tests/test_core.py"],
            },
            {
                "branch": "feat/large-refactor",
                "tier": "P1",
                "domain": "Storage",
                "ahead": 5,
                "behind": 10,
                "churn": 2500,
                "file_count": 25,
                "clean_merge": False,
                "dirty": True,
                "open_waves": "Wave 4",
                "completed_waves": "1-3",
                "next_action": "Review and proceed",  # Vague action that should be hardened
                "changed_files": ["storage/db.py", "dual_pipeline/engine.py"],
            },
            {
                "branch": "stash@{0}",
                "tier": "W",
                "domain": "Housekeeping",
                "classification": "DIRTY_STASH",
                "next_action": "git stash drop",
                "review_verdict": "STASH",
            },
        ]

    def test_executive_table_has_9_columns_and_header(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1})
        output = buf.getvalue()

        # Check 10-column header
        expected_header = "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
        self.assertIn(expected_header, output)
        expected_sep = "|:---:|:----------------------------------:|:---|:---:|:---:|:---:|:---:|:---:|:---|:---|"
        self.assertIn(expected_sep, output)

    def test_executive_table_renders_complexity_sizes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1})
        output = buf.getvalue()

        # Check that XS (for small feat) and L/XL (for large refactor) are rendered in table rows
        lines = output.splitlines()
        candidate_rows = [l for l in lines if l.startswith("| **1** |") or l.startswith("| **2** |")]
        self.assertEqual(len(candidate_rows), 2)

        # Row 1 should have XS complexity
        self.assertIn("| **XS** |", candidate_rows[0])
        # Row 2 should have L or XL complexity
        self.assertTrue("| **L** |" in candidate_rows[1] or "| **XL** |" in candidate_rows[1])

    def test_collapsed_stashes_row_has_9_columns(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1})
        output = buf.getvalue()

        lines = output.splitlines()
        collapsed_rows = [l for l in lines if "collapsed]" in l]
        self.assertEqual(len(collapsed_rows), 1)
        # Verify 10 columns separated by pipe (| — | ... | Housekeeping | — | N/A | N | N | N | ... | ... |)
        cells = [c.strip() for c in collapsed_rows[0].split("|") if c]
        self.assertEqual(len(cells), 10)
        self.assertEqual(cells[3], "—")  # Complexity column for collapsed stashes

    def test_action_item_is_hardened_against_vague_strings(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1})
        output = buf.getvalue()

        # "Review and proceed" must NOT be in the table; it should be hardened
        self.assertNotIn("Review and proceed", output)
        self.assertIn("before rebasing onto main", output)

    def test_council_deliberation_rendered_when_show_council_true(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1}, show_council=True)
        output = buf.getvalue()

        self.assertIn("6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", output)
        self.assertIn("Merge #1 `feat/first-ready`", output)
        self.assertIn("before #2 `feat/large-refactor`", output)

    def test_council_deliberation_omitted_when_show_council_false(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_executive_table(self.sample_queue, inv_counts={"branches": 2, "stashes": 1}, show_council=False)
        output = buf.getvalue()

        self.assertNotIn("6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", output)

    def test_render_queue_table_passes_show_council(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(
                self.sample_queue,
                inv_counts={"branches": 2, "stashes": 1},
                view_mode="executive",
                show_council=True,
            )
        output = buf.getvalue()
        self.assertIn("6-AGENT COUNCIL DELIBERATION & MERGE SEQUENCE RATIONALIZATION", output)


if __name__ == "__main__":
    unittest.main()
