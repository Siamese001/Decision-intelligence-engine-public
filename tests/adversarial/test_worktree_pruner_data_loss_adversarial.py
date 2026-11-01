"""
tests/adversarial/test_worktree_pruner_data_loss_adversarial.py

Adversarial data loss prevention tests:
1. Output collision preservation (zero byte loss on path collisions with differing content).
2. Refusal to prune or quarantine directories containing unique deliverables.
3. Strict prune without valid token is a read-only no-op preserving directory mtimes.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_worktree_hygiene import audit_hygiene
from tools.audit_worktree_hygiene_core.pruner import (
    cleanup_orphaned_worktree_directories,
    preserve_worktree_outputs,
)


class TestWorktreePrunerDataLossAdversarial(unittest.TestCase):
    """Adversarial validation of zero-data-loss pruning invariants."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "anti_loss@example.com"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Anti Loss Tester"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        (self.repo / "README.md").write_text("# Main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_output_collision_preservation_zero_byte_loss(self) -> None:
        """Verifies that divergent files sharing a path are both preserved without data loss."""
        # Main output file
        main_out = self.repo / "output" / "shared_report.json"
        main_out.parent.mkdir(parents=True, exist_ok=True)
        main_content = b'{"version": "main_authority"}'
        main_out.write_bytes(main_content)

        # Worktree output file with same relative path but divergent content
        orphan = self.wt_dir / "live_colliding_run"
        orphan.mkdir()
        wt_out = orphan / "output" / "shared_report.json"
        wt_out.parent.mkdir(parents=True, exist_ok=True)
        wt_content = b'{"version": "orphan_divergent_data"}'
        wt_out.write_bytes(wt_content)

        # Run preserve_worktree_outputs
        preserved = preserve_worktree_outputs(orphan, self.repo)
        self.assertEqual(preserved, 1)

        # Assert main file unchanged
        self.assertEqual(main_out.read_bytes(), main_content)

        # Assert collision file created and holds exact divergent content
        collision_file = self.repo / "output" / "shared_report_collision_live_colliding_run.json"
        self.assertTrue(collision_file.is_file(), f"Collision file not found at {collision_file}")
        self.assertEqual(collision_file.read_bytes(), wt_content)

    def test_deliverables_orphan_refused_from_prune(self) -> None:
        """Verifies that an orphan containing deliverables is strictly refused from pruning."""
        orphan = self.wt_dir / "live_uncommitted_meeting"
        orphan.mkdir()
        (orphan / "output").mkdir()
        transcript = orphan / "output" / "01_live_transcript.md"
        transcript.write_text("# Authoritative Live Transcript\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktree_directories(
            self.repo, confirm_token="CONFIRMED_STRICT_PRUNE"
        )
        self.assertNotIn("live_uncommitted_meeting", cleaned)
        self.assertTrue(orphan.is_dir(), "Orphan containing deliverables was improperly pruned!")
        self.assertTrue(transcript.is_file())

    def test_strict_prune_without_token_is_read_only_noop(self) -> None:
        """Verifies that strict prune without token does not modify filesystem."""
        orphan = self.wt_dir / "live_unconfirmed_stub"
        orphan.mkdir()
        (orphan / "output").mkdir()
        stub = orphan / "output" / "session_state.json"
        stub.write_bytes(b'{"status": "pending"}')

        mtime_before = orphan.stat().st_mtime_ns

        # Run via audit_hygiene without confirm_token
        report = audit_hygiene(self.repo, strict_prune=True)
        self.assertTrue(orphan.is_dir())
        self.assertEqual(orphan.stat().st_mtime_ns, mtime_before)
        self.assertTrue(any("dry-run" in s for s in report.cleaned_orphaned_worktrees))


if __name__ == "__main__":
    unittest.main()
