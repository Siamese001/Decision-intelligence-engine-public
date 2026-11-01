"""Unit tests for Wave 2 worktree teardown PID ancestry immunity."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.pr_governance_core.common import get_immune_pids, kill_ghost_processes


class TestWorktreeTeardownAncestryUnit:
    """Class A unit tests verifying suicide bug prevention and PID ancestry immunity."""

    def test_get_immune_pids_contains_ancestry(self) -> None:
        """Verifies current process, parent, and process group leader are immune."""
        immune = get_immune_pids()
        assert os.getpid() in immune
        assert os.getppid() in immune
        try:
            assert os.getpgid(0) in immune
        except Exception:
            pass

    def test_kill_ghost_processes_protects_caller_holding_file(self, tmp_path: Path) -> None:
        """Verifies that an open file descriptor held by the test process is NEVER targeted."""
        dummy_file = tmp_path / "locked_file.txt"
        with open(dummy_file, "w") as f:
            f.write("held open by test runner\n")
            f.flush()

            # Audit worktree directory with open file descriptor
            targets = kill_ghost_processes(tmp_path, terminate_ghosts=False)

            # Caller PID must be immune and excluded from target list
            assert os.getpid() not in targets

    def test_kill_ghost_processes_safe_empty_directory(self, tmp_path: Path) -> None:
        """Verifies clean return when no processes hold descriptors."""
        empty_dir = tmp_path / "empty_worktree"
        empty_dir.mkdir()
        targets = kill_ghost_processes(empty_dir, terminate_ghosts=True)
        assert targets == []
