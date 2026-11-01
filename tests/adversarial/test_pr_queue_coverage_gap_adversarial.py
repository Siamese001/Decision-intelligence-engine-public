"""
tests/adversarial/test_pr_queue_coverage_gap_adversarial.py

Adversarial and edge-case testing for PR queue worktree coverage:
1. Coverage gap detection: fails closed with WORKTREE_COVERAGE_GAP if any worktree is dropped.
2. Anti-data-loss dirty gate: never suggests pruning orphan folders with uncommitted code or unique deliverables.
3. Stash resilience: deleted branches, weird commit messages, and high stash volume handling.
4. Malformed and adversarial payloads: unicode, empty fields, and extreme boundaries.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pr_governance_core import inspection
from tools.pr_governance_core.queue_actions import explain_worktree_action


class TestPRQueueCoverageGapAdversarial(unittest.TestCase):
    """Adversarial stress testing for PR queue coverage and fail-closed gates."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "adv@example.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Adversary"], cwd=self.repo, check=True, capture_output=True)

        (self.repo / "README.md").write_text("# Main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_worktree_coverage_gap_fails_closed(self) -> None:
        """Verifies that an unlisted worktree folder triggers WORKTREE_COVERAGE_GAP and returns 1."""
        orphan = self.wt_dir / "live_orphan_gap"
        orphan.mkdir()
        (orphan / "dummy.txt").write_text("dummy", encoding="utf-8")

        args = argparse.Namespace(json=False, receipt=None, llm_summary=False)

        # Mock build_worktree_inventory to return empty worktrees list, creating a coverage gap
        mock_inv = {
            "counts": {"total_worktrees_children": 0, "stashes_total": 0},
            "worktrees": [],
            "stashes": [],
        }

        stderr_buf = io.StringIO()
        with patch.object(inspection, "ROOT", self.repo), \
             patch("tools.pr_governance_core.inspection.get_feature_branches", return_value=[]), \
             patch("tools.pr_governance_core.inspection.list_active_worktrees", return_value=[]), \
             patch("tools.pr_governance_core.inspection.build_worktree_inventory", return_value=mock_inv), \
             patch("sys.stderr", stderr_buf):
            ret = inspection.cmd_list(args)

        self.assertEqual(ret, 1, f"Expected cmd_list to return 1 on coverage gap, got {ret}")
        err_out = stderr_buf.getvalue()
        self.assertIn("WORKTREE_COVERAGE_GAP", err_out)
        self.assertIn("live_orphan_gap", err_out)

    def test_dirty_gate_preserves_unique_deliverables(self) -> None:
        """Verifies that an orphan worktree with unique deliverables is NEVER recommended for prune/quarantine."""
        item = {
            "classification": "ORPHAN_DELIVERABLES",
            "name": "live_important_client_call",
            "unique_files": [{"path": "output/01_hitl_resolved_transcript.md"}],
        }
        action = explain_worktree_action(item)
        self.assertTrue(action.startswith("[RECOVER_DELIVERABLES]"), f"Expected RECOVER_DELIVERABLES, got: {action}")
        self.assertNotIn("[PRUNE]", action)
        self.assertNotIn("[QUARANTINE", action)
        self.assertIn("01_hitl_resolved_transcript.md", action)

    def test_dirty_gate_preserves_audio_recordings(self) -> None:
        """Verifies audio-only orphan folders get QUARANTINE_AUDIO, never destructive PRUNE."""
        item = {
            "classification": "ORPHAN_AUDIO_ONLY",
            "name": "live_audio_session",
            "unique_files_count": 3,
        }
        action = explain_worktree_action(item)
        self.assertTrue(action.startswith("[QUARANTINE_AUDIO]"), f"Expected QUARANTINE_AUDIO, got: {action}")
        self.assertNotIn("[PRUNE]", action)

    def test_dirty_gate_preserves_receipts_only(self) -> None:
        """Verifies receipts-only orphan folders get QUARANTINE_RECEIPTS, never destructive PRUNE."""
        item = {
            "classification": "ORPHAN_RECEIPTS_ONLY",
            "name": "live_receipts_session",
            "unique_files_count": 2,
        }
        action = explain_worktree_action(item)
        self.assertTrue(action.startswith("[QUARANTINE_RECEIPTS]"), f"Expected QUARANTINE_RECEIPTS, got: {action}")
        self.assertNotIn("[PRUNE]", action)

    def test_stash_triage_resilience_to_deleted_branches(self) -> None:
        """Verifies stash on a deleted branch generates clear [TRIAGE_STASH] with branch status."""
        item = {
            "classification": "STASH_DELETED_BRANCH",
            "stash_ref": "stash@{0}",
            "subject": "WIP on feat/dead-branch: some fix",
        }
        action = explain_worktree_action(item)
        self.assertTrue(action.startswith("[TRIAGE_STASH]"), f"Expected TRIAGE_STASH, got: {action}")
        self.assertIn("branch deleted", action)

    def test_stash_triage_on_main(self) -> None:
        """Verifies stash on main generates clear [TRIAGE_STASH] indicating on main."""
        item = {
            "classification": "STASH_MAIN",
            "stash_ref": "stash@{1}",
            "subject": "WIP on main: exploratory work",
        }
        action = explain_worktree_action(item)
        self.assertTrue(action.startswith("[TRIAGE_STASH]"), f"Expected TRIAGE_STASH, got: {action}")
        self.assertIn("on main", action)

    def test_malformed_and_boundary_stash_subjects(self) -> None:
        """Verifies robust handling of empty, unicode, and special-character subjects."""
        edge_cases = [
            {"classification": "STASH_MAIN", "stash_ref": "stash@{0}", "subject": ""},
            {"classification": "STASH_DELETED_BRANCH", "stash_ref": "stash@{1}", "subject": "🔥 Unicode test ⚡️"},
            {"classification": "STASH_LIVE_BRANCH", "stash_ref": "stash@{2}", "subject": "Special 'chars' \"in\" [subject] | table"},
        ]
        for ec in edge_cases:
            action = explain_worktree_action(ec)
            self.assertTrue(action.startswith("[TRIAGE_STASH]"), f"Failed for {ec}: got {action}")

    def test_cli_invocation_clean_on_empty_repo(self) -> None:
        """CLI invocation test: verifies pr_governance list on synthetic repo succeeds normally."""
        script = REPO_ROOT / "tools" / "pr_governance.py"
        proc = subprocess.run(
            [sys.executable, str(script), "list"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"pr_governance list failed: {proc.stderr}")
        self.assertIn("Worktree coverage: 0/0 children · 0/0 stashes", proc.stdout)


if __name__ == "__main__":
    unittest.main()
