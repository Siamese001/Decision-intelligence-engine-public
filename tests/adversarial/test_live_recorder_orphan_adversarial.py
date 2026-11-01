"""
tests/adversarial/test_live_recorder_orphan_adversarial.py

Adversarial stress testing for live recorder worktree isolation and zero-orphan invariants:
1. Crash / exit before first chunk: signal handler or atexit teardown leaves 0 orphans.
2. Concurrent starts on same slug: LiveSessionCollisionError raised without orphan leakage.
3. Teardown safety refusal: teardown_empty_live_worktree preserves directories with substantive files.
4. Unmanaged directory collision handling: never clobbers or writes blindly into existing plain folder.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    teardown_empty_live_worktree,
)
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory


class TestLiveRecorderOrphanAdversarial(unittest.TestCase):
    """Adversarial stress tests for live recorder zero-orphan guarantees."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "adv@example.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Adversary Runner"], cwd=self.repo, check=True, capture_output=True)

        (self.repo / "README.md").write_text("# Main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_crash_before_first_chunk_cleans_up(self) -> None:
        """Simulates crash or exit before any audio chunk is processed."""
        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.repo):
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Crash Before Chunk",
                mock_mode=True,
                enforce_worktree=True,
                output_dir="auto",
                enable_ws=False,
                enable_copilot=False,
            )

            # Trigger atexit/signal handler cleanup
            recorder._early_abort_cleanup()

        inv = build_worktree_inventory(self.repo)
        self.assertEqual(inv["counts"]["orphan_directories"], 0)
        self.assertEqual(inv["counts"]["total_worktrees_children"], 0)

    def test_concurrent_starts_raise_collision_without_orphan(self) -> None:
        """Two concurrent recorders for the same meeting fail closed via LiveSessionCollisionError."""
        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.repo):
            rec1 = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Contested Slug Session",
                mock_mode=True,
                enforce_worktree=True,
                output_dir="auto",
                enable_ws=False,
                enable_copilot=False,
            )

            with self.assertRaises(LiveSessionCollisionError):
                LiveMeetingRecorder(
                    project_name="project_nexus",
                    meeting_name="Contested Slug Session",
                    mock_mode=True,
                    enforce_worktree=True,
                    output_dir="auto",
                    enable_ws=False,
                    enable_copilot=False,
                )

            rec1._early_abort_cleanup()

        inv = build_worktree_inventory(self.repo)
        self.assertEqual(inv["counts"]["orphan_directories"], 0)

    def test_teardown_empty_live_worktree_preserves_substantive_files(self) -> None:
        """teardown_empty_live_worktree refuses to delete folders that contain deliverables."""
        unmanaged_folder = self.wt_dir / "live_important_deliverables"
        unmanaged_folder.mkdir(exist_ok=True)
        deliv = unmanaged_folder / "output" / "01_transcript.md"
        deliv.parent.mkdir(parents=True, exist_ok=True)
        deliv.write_text("# Deliverable", encoding="utf-8")

        # Call teardown on this directory
        teardown_empty_live_worktree(unmanaged_folder, "feat/live-important-deliverables", self.repo)

        # Directory must be preserved because it has substantive files!
        self.assertTrue(unmanaged_folder.exists(), "Directory with deliverables was destroyed!")
        self.assertTrue(deliv.exists(), "Deliverable file was destroyed!")

    def test_teardown_empty_live_worktree_removes_purely_empty_folder(self) -> None:
        """teardown_empty_live_worktree removes a folder containing only empty subdirs or locks."""
        empty_stub = self.wt_dir / "live_empty_stub"
        empty_stub.mkdir(exist_ok=True)
        (empty_stub / "empty_sub").mkdir(exist_ok=True)
        (empty_stub / ".flock").write_text("lock", encoding="utf-8")

        teardown_empty_live_worktree(empty_stub, "feat/live-empty-stub", self.repo)

        self.assertFalse(empty_stub.exists(), "Empty stub should have been cleaned up!")


if __name__ == "__main__":
    unittest.main()
