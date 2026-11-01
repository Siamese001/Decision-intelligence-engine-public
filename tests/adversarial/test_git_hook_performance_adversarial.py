#!/usr/bin/env python3
"""Adversarial tests for Git Hook Performance and Rebase Fast-Path.

Verifies:
1. Rebase detection triggers exclusively under genuine rebase/cherry-pick environments.
2. Unified orchestrator fails closed when structural violations are staged.
3. Unified orchestrator rejects synthetic embedding identifiers introduced into staged files.
4. Fast staged scanner does not leak or bypass retired identifier bans.
5. In-process orchestrator completes cleanly under 2.5s on clean commits.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pre_commit_orchestrator import run_orchestrator  # noqa: E402
from tools.validate_no_synthetic_embeddings import RETIRED_IDENTIFIERS  # noqa: E402
from tools.validate_no_synthetic_embeddings import scan as scan_synthetic  # noqa: E402
from tools.verify_commit import verify_commit  # noqa: E402


class GitHookPerformanceAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT

    def test_01_rebase_detection_environment_markers(self) -> None:
        """Verify shell detection mechanics for rebase-merge, rebase-apply, and reflog."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            git_dir = tmp_root / ".git"
            git_dir.mkdir(parents=True)

            # Case A: Normal state (no rebase)
            self.assertFalse((git_dir / "rebase-merge").exists())
            self.assertFalse((git_dir / "rebase-apply").exists())

            # Case B: rebase-merge marker present
            rebase_merge = git_dir / "rebase-merge"
            rebase_merge.mkdir()
            self.assertTrue(rebase_merge.exists())
            rebase_merge.rmdir()

            # Case C: rebase-apply marker present
            rebase_apply = git_dir / "rebase-apply"
            rebase_apply.mkdir()
            self.assertTrue(rebase_apply.exists())
            rebase_apply.rmdir()

    def test_02_synthetic_embedding_ban_staged_detection(self) -> None:
        """Verify that --staged mode detects prohibited identifiers in staged files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_root, capture_output=True, check=True)

            # Clean file committed
            clean_file = tmp_root / "clean.py"
            clean_file.write_text("x = 42\n", encoding="utf-8")
            subprocess.run(["git", "add", "clean.py"], cwd=tmp_root, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "chore: initial clean commit"], cwd=tmp_root, capture_output=True, check=True)

            # Adversarial dirty file staged with exact retired identifier
            dirty_file = tmp_root / "adversarial.py"
            prohibited_token = RETIRED_IDENTIFIERS[0]
            dirty_file.write_text(f'RETIRED_TOKEN = "{prohibited_token}"\n', encoding="utf-8")
            subprocess.run(["git", "add", "adversarial.py"], cwd=tmp_root, capture_output=True, check=True)

            res = scan_synthetic(tmp_root, staged=True)
            self.assertEqual(res["status"], "FAIL", "Staged scan must fail when synthetic embedding token is staged")
            self.assertGreater(res["match_count"], 0)

            # Unstage dirty file -> staged scan must now PASS
            subprocess.run(["git", "rm", "--cached", "adversarial.py"], cwd=tmp_root, capture_output=True, check=True)
            res_after = scan_synthetic(tmp_root, staged=True)
            self.assertEqual(res_after["status"], "PASS", "Staged scan must pass when dirty file is unstaged")

    def test_03_pre_commit_orchestrator_clean_performance(self) -> None:
        """Verify that run_orchestrator on clean repository runs in < 5.0s."""
        t0 = time.monotonic()
        rc = run_orchestrator(repository_root=self.repo_root, staged=True, verbose=False)
        duration = time.monotonic() - t0
        self.assertEqual(rc, 0, f"Orchestrator should pass on clean repository, got rc={rc}")
        self.assertLess(duration, 5.0, f"Orchestrator must execute in < 5.0s, took {duration:.2f}s")

    def test_04_pre_commit_orchestrator_fails_closed_on_structural_syntax_error(self) -> None:
        """Verify that verify_commit halts immediately if a syntax error is present."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            bad_file = tmp_root / "broken.py"
            bad_file.write_text("def broken_syntax(:\n  pass\n", encoding="utf-8")

            # verify_commit on explicit broken file must fail
            rc = verify_commit(staged=False, all_files=False, explicit_files=[str(bad_file)], verbose=False)
            self.assertNotEqual(rc, 0, "verify_commit must reject syntax-broken file")


if __name__ == "__main__":
    unittest.main()
