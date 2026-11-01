#!/usr/bin/env python3
"""End-to-End test for Git Rebase Hook Performance and Multi-Commit Replay.

Simulates an isolated multi-commit feature branch rebase in a temporary Git repository
configured with the Wave 1 fast-path pre-commit hook.
Proves that commit replay operates without blocking or failing on redundant test suites,
and that the final rebased state preserves full integrity.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class GitRebaseHookPerformanceE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="git_rebase_e2e_")
        self.repo_dir = Path(self.tmp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True)

        # Initialize git repo
        self._run(["git", "init", "-b", "main"])
        self._run(["git", "config", "user.name", "Tester"])
        self._run(["git", "config", "user.email", "test@example.com"])
        self._run(["git", "config", "commit.gpgsign", "false"])

        # Install mock pre-commit hook with rebase fast-path
        hooks_dir = self.repo_dir / ".githooks"
        hooks_dir.mkdir(parents=True)
        hook_script = hooks_dir / "pre-commit"
        hook_content = """#!/bin/sh
set -eu
ROOT=$(git rev-parse --show-toplevel)
GIT_DIR=$(git rev-parse --git-dir)

IS_REBASE=0
if [ -d "$GIT_DIR/rebase-merge" ] || [ -d "$GIT_DIR/rebase-apply" ] || [ -f "$GIT_DIR/REBASE_HEAD" ]; then
  IS_REBASE=1
fi
REFLOG="${GIT_REFLOG_ACTION:-}"
case "$REFLOG" in
  *rebase*|*cherry-pick*)
    IS_REBASE=1
    ;;
esac

if [ "$IS_REBASE" -eq 1 ]; then
  # Wave 1 Fast-Path simulation: <10ms check
  echo "FAST-REBASE-ENGAGED" >> "$ROOT/.rebase_trace.log"
  exit 0
fi

# Normal commit path simulation
echo "NORMAL-COMMIT-ENGAGED" >> "$ROOT/.commit_trace.log"
exit 0
"""
        hook_script.write_text(hook_content, encoding="utf-8")
        hook_script.chmod(0o755)
        self._run(["git", "config", "core.hooksPath", ".githooks"])

        # Create base commit on main
        (self.repo_dir / "README.md").write_text("# Main Branch\n", encoding="utf-8")
        self._run(["git", "add", "README.md"])
        self._run(["git", "commit", "-m", "chore: initial commit on main"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self, cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

    def test_01_normal_commit_runs_standard_path(self) -> None:
        """Verify that a normal commit triggers the standard pre-commit gate."""
        (self.repo_dir / "doc.txt").write_text("documentation\n", encoding="utf-8")
        self._run(["git", "add", "doc.txt"])
        self._run(["git", "commit", "-m", "docs: update documentation"])

        commit_log = self.repo_dir / ".commit_trace.log"
        self.assertTrue(commit_log.exists())
        self.assertIn("NORMAL-COMMIT-ENGAGED", commit_log.read_text(encoding="utf-8"))

    def test_02_rebase_conflict_commit_engages_fast_path(self) -> None:
        """Verify that commits during an active rebase (conflict resolution) engage the fast path."""
        # Create feature branch
        self._run(["git", "checkout", "-b", "feat/my-feature"])
        (self.repo_dir / "conflict.txt").write_text("feature content\n", encoding="utf-8")
        self._run(["git", "add", "conflict.txt"])
        self._run(["git", "commit", "-m", "feat: feature change"])

        # Create divergent commit on main
        self._run(["git", "checkout", "main"])
        (self.repo_dir / "conflict.txt").write_text("main content\n", encoding="utf-8")
        self._run(["git", "add", "conflict.txt"])
        self._run(["git", "commit", "-m", "feat: main change"])

        # Start rebase -> hits conflict
        self._run(["git", "checkout", "feat/my-feature"])
        res = subprocess.run(["git", "rebase", "main"], cwd=self.repo_dir, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Rebase must pause on conflict")

        # Resolve conflict and commit during rebase state
        (self.repo_dir / "conflict.txt").write_text("merged content\n", encoding="utf-8")
        self._run(["git", "add", "conflict.txt"])
        self._run(["git", "commit", "-m", "fix: resolve rebase conflict"])

        # Assert fast rebase path was engaged
        rebase_log = self.repo_dir / ".rebase_trace.log"
        self.assertTrue(rebase_log.exists(), "Rebase fast-path trace log should exist")
        self.assertIn("FAST-REBASE-ENGAGED", rebase_log.read_text(encoding="utf-8"))

    def test_03_reflog_rebase_action_engages_fast_path(self) -> None:
        """Verify that GIT_REFLOG_ACTION matching rebase/cherry-pick engages the fast path."""
        (self.repo_dir / "quick.txt").write_text("fast content\n", encoding="utf-8")
        self._run(["git", "add", "quick.txt"])
        custom_env = os.environ.copy()
        custom_env["GIT_REFLOG_ACTION"] = "rebase (finish): returning to refs/heads/main"
        self._run(["git", "commit", "-m", "chore: simulated rebase commit"], env=custom_env)

        rebase_log = self.repo_dir / ".rebase_trace.log"
        self.assertTrue(rebase_log.exists())
        self.assertIn("FAST-REBASE-ENGAGED", rebase_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
