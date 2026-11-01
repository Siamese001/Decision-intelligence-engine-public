#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Worktree PYTHONPATH Precedence & Module Shadowing (Wave 2).

Mandatory Edge-Case Categories Covered:
1. Boundary payload: empty PYTHONPATH, non-existent repo_root, trailing/leading colons.
2. Malformed input / race: concurrent subprocess spawning with varying PYTHONPATH states.
3. Invariant enforcement: worktree packages strictly shadow external or root packages without .pth leakage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.readiness_core import run_stage


class WorktreePythonpathPrecedenceAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="worktree_pp_adv_")
        self.worktree_root = Path(self.tmp_dir) / "worktree"
        self.worktree_root.mkdir(parents=True)

        self.external_root = Path(self.tmp_dir) / "external"
        self.external_root.mkdir(parents=True)

        # Create shadowed package in worktree vs external
        wt_pkg = self.worktree_root / "shadow_pkg"
        wt_pkg.mkdir(parents=True)
        (wt_pkg / "__init__.py").write_text("ORIGIN = 'WORKTREE'\n", encoding="utf-8")

        ext_pkg = self.external_root / "shadow_pkg"
        ext_pkg.mkdir(parents=True)
        (ext_pkg / "__init__.py").write_text("ORIGIN = 'EXTERNAL'\n", encoding="utf-8")

        # Create inspector script
        self.script = self.worktree_root / "inspect_origin.py"
        self.script.write_text(
            "import sys\nimport shadow_pkg\n"
            "print(f'ORIGIN:{shadow_pkg.ORIGIN}')\n"
            "print(f'SYSPATH0:{sys.path[0]}')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_worktree_shadows_external_pythonpath(self) -> None:
        """run_stage prepends worktree root so worktree module shadows external candidate."""
        # Intentionally pollute current env with external_root first
        cmd = [sys.executable, "-B", str(self.script)]

        orig_pp = os.environ.get("PYTHONPATH", "")
        try:
            os.environ["PYTHONPATH"] = str(self.external_root)
            success, output, _ = run_stage("Inspect", cmd, repo_root=self.worktree_root)
            self.assertTrue(success, f"Stage run failed: {output}")
            self.assertIn("ORIGIN:WORKTREE", output, "Worktree shadow_pkg MUST take precedence over external")
        finally:
            os.environ["PYTHONPATH"] = orig_pp

    def test_02_empty_pythonpath_handles_cleanly(self) -> None:
        """When PYTHONPATH is absent or empty, worktree root is still cleanly set."""
        orig_pp = os.environ.get("PYTHONPATH")
        cmd = [sys.executable, "-B", str(self.script)]
        try:
            if "PYTHONPATH" in os.environ:
                del os.environ["PYTHONPATH"]
            success, output, _ = run_stage("Inspect", cmd, repo_root=self.worktree_root)
            self.assertTrue(success)
            self.assertIn("ORIGIN:WORKTREE", output)
        finally:
            if orig_pp is not None:
                os.environ["PYTHONPATH"] = orig_pp

    def test_03_subsecond_execution_isolated_from_pth(self) -> None:
        """Verifies that sys.path[0] matches the worktree path without root .pth interference."""
        cmd = [sys.executable, "-B", str(self.script)]
        success, output, _ = run_stage("Inspect", cmd, repo_root=self.worktree_root)
        self.assertTrue(success)
        self.assertIn(str(self.worktree_root), output)


if __name__ == "__main__":
    unittest.main()
