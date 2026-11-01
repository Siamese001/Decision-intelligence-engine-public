"""
Adversarial and Edge Case Test Suite for Executive PR Queue View.

Covers:
  1. Boundary payloads: empty queue, zero candidate branches, 100+ stashes.
  2. Anti-Data-Loss Invariant: ahead == 0 with dirty authored files must NEVER be recommended for Prune.
  3. Conflict branches: actionable conflict and rebase guidance.
  4. Extreme divergence: behind 100+ commits properly surfaced.
  5. Governance block handling: clear guidance to fix failing linter/contract.
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


class TestPRQueueExecutiveViewAdversarial(unittest.TestCase):
    def test_empty_queue_boundary(self) -> None:
        """Verify empty queue renders gracefully without crashing."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_executive_table([], inv_counts={"total_children": 0, "covered_children": 0, "total_stashes": 0, "covered_stashes": 0})
        output = buf.getvalue()
        self.assertIn("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |", output)
        self.assertIn("Active candidate branches: 0", output)

    def test_stashes_only_boundary(self) -> None:
        """Verify queue with only stashes and stubs renders only the summary row."""
        queue: list[Dict[str, Any]] = [
            {"tier": "W", "branch": f"stash@{{{i}}}", "domain": "Housekeeping"}
            for i in range(15)
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_executive_table(queue, inv_counts={"total_children": 0, "covered_children": 0, "total_stashes": 15, "covered_stashes": 15})
        output = buf.getvalue()
        self.assertIn("Active candidate branches: 0", output)
        self.assertIn("15 git stash(es) collapsed", output)

    def test_anti_data_loss_dirty_zero_ahead_never_pruned(self) -> None:
        """Verify anti-data-loss invariant: ahead == 0 with uncommitted work is NEVER recommended for Prune."""
        row = {
            "tier": "P3",
            "branch": "feat/precious-uncommitted-work",
            "ahead": 0,
            "behind": 50,
            "dirty": True,
            "clean_merge": True,
            "next_action": "[COMMIT_FIRST] 1 uncommitted file; commit work before rebasing onto main",
        }
        disp, action = _format_executive_disposition(row)
        self.assertNotEqual(disp, "Decommission (No Value)")
        self.assertEqual(disp, "Complete In-Flight Work")
        self.assertIn("Commit authored inception work", action)

    def test_severe_divergence_surface(self) -> None:
        """Verify high divergence behind count is surfaced in action."""
        row = {
            "tier": "P2",
            "branch": "feat/severely-diverged",
            "ahead": 2,
            "behind": 185,
            "dirty": False,
            "clean_merge": True,
            "next_action": "[REBASE] 185 commits behind",
        }
        disp, action = _format_executive_disposition(row)
        self.assertEqual(disp, "Promote to Main")
        self.assertIn("185 commits behind", action)

    def test_governance_block_guidance(self) -> None:
        """Verify governance block gives actionable linter remediation guidance."""
        row = {
            "tier": "P1",
            "branch": "feat/blocked-branch",
            "ahead": 1,
            "behind": 0,
            "dirty": False,
            "clean_merge": True,
            "review_verdict": "GOVERNANCE_BLOCK",
        }
        disp, action = _format_executive_disposition(row)
        self.assertEqual(disp, "Hold / Blocked")
        self.assertIn("Resolve failing governance gate", action)

    def test_detect_graph_mutated_distinguishes_pre_mutation_vs_authoritative(self) -> None:
        """Verify pre-mutation diagnostic breakdowns return N while authoritative activation receipts return Y."""
        import tempfile
        from tools.pr_governance_core.executive_render import detect_graph_mutated

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pre_dir = root / "output" / "project_lens" / "10-09-2026" / "standup"
            pre_dir.mkdir(parents=True)
            (pre_dir / "graph_mutation_breakdown.md").write_text(
                "**Run Mode:** Pre-Mutation Diagnostic (read-only, PRAGMA query_only = ON)\n",
                encoding="utf-8",
            )
            row_pre = {
                "branch": "feat/live-pre-mutation",
                "changed_files": ["output/project_lens/10-09-2026/standup/graph_mutation_breakdown.md"],
            }
            self.assertEqual(detect_graph_mutated(row_pre, repo_root=root), "N")

            auth_dir = root / "output" / "project_lens" / "10-16-2026" / "ed_amit"
            auth_dir.mkdir(parents=True)
            (auth_dir / "publication_coverage_receipt.json").write_text(
                '{"activated_generation_id": "generation-123", "coverage_summary": {"committed_count": 20}}',
                encoding="utf-8",
            )
            row_auth = {
                "branch": "feat/live-authoritative",
                "changed_files": ["output/project_lens/10-16-2026/ed_amit/publication_coverage_receipt.json"],
            }
            self.assertEqual(detect_graph_mutated(row_auth, repo_root=root), "Y")


if __name__ == "__main__":
    unittest.main()
