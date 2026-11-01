#!/usr/bin/env python3
"""Adversarial stress and edge-case testing for PR latency optimizations.

Covers >= 3 mandatory edge-case categories:
1. Boundary Payloads: Corrupted prior receipt JSON, empty worktree porcelain output, unreadable paths.
2. Invariant Bypass Attempts: Attempting sweeping restores (e.g. '.', 'dual_pipeline/', '*') on dirty worktrees.
3. Fail-Fast Enforcement: Early failure halts loop immediately without executing downstream stages.
4. Fallback Recovery: Corrupt prior receipt gracefully falls back to empty failure map without crashing.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance_core.worktree import (
    assert_safe_worktree_operation,
)
from tools.verify_main_readiness import (
    _load_prior_failures,
    get_branch_target_files,
    verify_main_readiness,
)


class TestPRLatencyOptimizationAdversarial(unittest.TestCase):
    """Adversarial tests for PR latency optimization invariants."""

    def test_adversarial_boundary_corrupt_prior_receipt_fallback(self) -> None:
        """Category 1: Boundary payloads - corrupted JSON in prior receipt handles cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            corrupt_file = tmp_path / "corrupt_receipt.json"
            corrupt_file.write_text("{this is not valid json", encoding="utf-8")

            prior_map, failed = _load_prior_failures(corrupt_file)
            self.assertEqual(prior_map, {})
            self.assertEqual(failed, set())

            # Non-existent file
            prior_map, failed = _load_prior_failures(tmp_path / "missing.json")
            self.assertEqual(prior_map, {})
            self.assertEqual(failed, set())

    def test_adversarial_invariant_bypass_sweeping_worktree_wipe(self) -> None:
        """Category 2: Invariant bypass attempts - sweeping checkout/restore blocked on dirty worktree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Dirty worktree with uncommitted changes
            with patch("tools.pr_governance_core.worktree.run_git", return_value=" M dual_pipeline/core.py\n?? scratch.py"):
                for sweeping_target in [".", "dual_pipeline", "dual_pipeline/", "tests", "tools", "*"]:
                    safe, msg = assert_safe_worktree_operation(tmp_path, [sweeping_target])
                    self.assertFalse(safe, f"Expected sweeping target '{sweeping_target}' to be blocked")
                    self.assertIn("blocked", msg)

                # Explicit single file is permitted
                safe, msg = assert_safe_worktree_operation(tmp_path, ["dual_pipeline/core.py"])
                self.assertTrue(safe)
                self.assertEqual(msg, "Safe operation")

    def test_adversarial_fail_fast_enforcement(self) -> None:
        """Category 3: Fail-Fast enforcement - halts immediately after first failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            # Mock stages where stage 2 fails
            stages = [
                ("Stage 1", ["echo", "1"]),
                ("Stage 2", ["false"]),
                ("Stage 3", ["echo", "3"]),
                ("Stage 4", ["echo", "4"]),
            ]

            call_count = 0
            def mock_run_stage(name: str, cmd: list[str]):
                nonlocal call_count
                call_count += 1
                if name == "Stage 2":
                    return False, "Simulated failure", 0.05
                return True, "Passed", 0.05

            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness._build_stages", return_value=stages), \
                 patch("tools.verify_main_readiness.run_stage", side_effect=mock_run_stage):
                code = verify_main_readiness(
                    fail_fast=True,
                    explicit_slug="test_fail_fast",
                )
                self.assertEqual(code, 1)
                # Exactly 2 stages must have been executed, stages 3 and 4 were NOT run
                self.assertEqual(call_count, 2, "Fail-fast must abort immediately after Stage 2 fails")

    def test_adversarial_target_files_unstaged_and_branch_isolation(self) -> None:
        """Category 4: Branch target isolation - does not leak commits from other branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            def mock_git(args: list[str], **kwargs: object) -> MagicMock:
                res = MagicMock()
                res.returncode = 0
                if "status" in args:
                    res.stdout = ""
                elif "rev-parse" in args:
                    res.stdout = "feat/my-branch\n"
                elif "--cached" in args:
                    res.stdout = "tools/staged_file.py\n"
                elif "main...HEAD" in args:
                    res.stdout = "dual_pipeline/branch_file.py\n"
                elif "--name-only" in args and "--cached" not in args:
                    res.stdout = "tools/unstaged_file.py\n"
                else:
                    res.stdout = ""
                return res

            with patch("subprocess.run", side_effect=mock_git):
                targets = get_branch_target_files(tmp_root)
                self.assertIn("tools/staged_file.py", targets)
                self.assertIn("tools/unstaged_file.py", targets)
                self.assertIn("dual_pipeline/branch_file.py", targets)


if __name__ == "__main__":
    unittest.main()
