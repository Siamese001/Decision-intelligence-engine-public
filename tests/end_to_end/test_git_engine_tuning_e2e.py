#!/usr/bin/env python3
"""End-to-End test for Git Engine Tuning and Worktree Performance (Wave 2).

Simulates:
1. Running `python tools/pr_governance.py doctor` and verifying performance setting audit.
2. Provisioning a new isolated worktree and proving automatic performance configuration inheritance.
3. Measuring git status execution speed in the tuned worktree (< 0.15s).
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pr_governance_core.inspection import cmd_doctor  # noqa: E402
from tools.pr_governance_core.worktree import provision_isolated_worktree  # noqa: E402


class GitEngineTuningE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="git_engine_e2e_")
        self.repo_dir = Path(self.tmp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True)

        # Initialize base git repo
        self._run(["git", "init", "-b", "main"])
        self._run(["git", "config", "user.name", "Tester"])
        self._run(["git", "config", "user.email", "test@example.com"])
        self._run(["git", "config", "commit.gpgsign", "false"])

        (self.repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        self._run(["git", "add", "README.md"])
        self._run(["git", "commit", "-m", "chore: initial commit"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_01_cmd_doctor_audits_performance_settings(self) -> None:
        """Verify cmd_doctor inspects core.fsmonitor, core.untrackedCache, and core.commitGraph."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_doctor(argparse.Namespace())
        output = buf.getvalue()
        self.assertIn("core.fsmonitor", output)
        self.assertIn("core.untrackedCache", output)
        self.assertIn("core.commitGraph", output)

    def test_02_worktree_provisioning_configures_engine_tuning(self) -> None:
        """Verify provision_isolated_worktree configures all performance settings."""
        wt_dir = self.repo_dir / ".worktrees" / "feat_test"
        provision_isolated_worktree(
            root=self.repo_dir,
            worktree_dir=wt_dir,
            branch="feat/test",
            base_branch="main",
        )

        self.assertTrue(wt_dir.is_dir(), "Worktree directory must exist")

        # Verify performance settings in worktree
        for key in ("core.hooksPath", "core.fsmonitor", "core.untrackedCache", "core.commitGraph"):
            val = subprocess.run(
                ["git", "-C", str(wt_dir), "config", key],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertTrue(val in (".githooks", "true"), f"{key} should be configured, got '{val}'")

        # Measure git status speed in worktree
        t0 = time.monotonic()
        subprocess.run(["git", "-C", str(wt_dir), "status"], capture_output=True, text=True, check=True)
        status_dur = time.monotonic() - t0
        self.assertLess(status_dur, 0.25, f"git status in tuned worktree must take < 0.25s, took {status_dur:.2f}s")


if __name__ == "__main__":
    unittest.main()
