"""tests/adversarial/test_readiness_overhead_and_cache_adversarial.py

Adversarial and regression test suite for Wave 2:
1. Partial-suite module caching: when 1 of N modules fails, all N-1 passing modules are cached and skipped on retry.
2. Worktree cleanliness: running tests or readiness does not dirty tracked files in git status.
3. Absolute diagnostic log resolution: full_log_path in stage failure diagnostics resolves to exact absolute path on disk.
4. Prompt export synchronization: --sync deterministically mirrors canonical prompt updates to hardened exports.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestReadinessOverheadAndCacheAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_cache_adv_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_partial_suite_module_caching_on_failure(self):
        """When 1 of 2 modules fails, the passing module must be cached and skipped on retry."""
        from tools.test_module_cache import (
            clear_module_cache,
            compute_module_fingerprint,
            get_cached_module,
        )

        # Create isolated repo structure
        repo = self.tmp_dir / "repo"
        repo.mkdir()
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").touch()
        (repo / ".local").mkdir()
        (repo / "reports").mkdir()

        # Module 1 passes, Module 2 fails
        mod1 = tests_dir / "test_mod_pass.py"
        mod1.write_text(
            "import unittest\nclass TestPass(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
            encoding="utf-8",
        )
        mod2 = tests_dir / "test_mod_fail.py"
        mod2.write_text(
            "import unittest\nclass TestFail(unittest.TestCase):\n    def test_bad(self):\n        self.fail('intentional')\n",
            encoding="utf-8",
        )

        fp1, _ = compute_module_fingerprint("tests.test_mod_pass", repo)
        fp2, _ = compute_module_fingerprint("tests.test_mod_fail", repo)

        runner = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{str(repo)}:{str(ROOT)}:{env.get('PYTHONPATH', '')}"

        # Run 1: with --cache-modules. Should fail overall because mod2 fails.
        cmd1 = [
            sys.executable,
            "-B",
            str(runner),
            "--cache-modules",
            "tests.test_mod_pass",
            "tests.test_mod_fail",
        ]
        proc1 = subprocess.run(cmd1, cwd=str(repo), env=env, capture_output=True, text=True)
        self.assertNotEqual(proc1.returncode, 0, "Suite must fail when mod2 fails")

        # Crucial assertion: mod1 passed, so its fingerprint MUST be cached!
        cached_entry1 = get_cached_module("tests.test_mod_pass", repo, fp1)
        self.assertIsNotNone(
            cached_entry1, "Passing module 1 must be saved to module cache despite partial suite failure"
        )
        self.assertEqual(cached_entry1.get("status"), "PASS")

        # mod2 failed, so its fingerprint must NOT be cached
        cached_entry2 = get_cached_module("tests.test_mod_fail", repo, fp2)
        self.assertIsNone(cached_entry2, "Failing module 2 must not be cached")

        # Fix module 2 so it passes
        mod2.write_text(
            "import unittest\nclass TestFail(unittest.TestCase):\n    def test_bad(self):\n        pass\n",
            encoding="utf-8",
        )

        # Run 2: retry with --cache-modules
        cmd2 = [
            sys.executable,
            "-B",
            str(runner),
            "--cache-modules",
            "tests.test_mod_pass",
            "tests.test_mod_fail",
        ]
        proc2 = subprocess.run(cmd2, cwd=str(repo), env=env, capture_output=True, text=True)
        self.assertEqual(proc2.returncode, 0, f"Retry should pass:\n{proc2.stderr}\n{proc2.stdout}")
        self.assertIn(
            "⚡ Module Cache: Reusing 1 cached PASS module(s)",
            proc2.stdout,
            "Passing module 1 must be reused from cache on retry",
        )

    def test_worktree_cleanliness_after_test_run(self):
        """Running tests in the worktree must not dirty tracked files in git status."""
        runner = ROOT / "tools" / "run_tests_with_progress.py"
        cmd = [
            sys.executable,
            "-B",
            str(runner),
            "tests.end_to_end.test_pr_council_complexity_e2e",
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Test run failed:\n{proc.stderr}")

        # Check git status for tracked modifications
        st = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        # Ensure reports/test_runner_receipt.json or test_timing_db.json are not modified
        dirty_lines = [
            line for line in st.stdout.splitlines()
            if line.startswith(" M ") and ("test_runner_receipt.json" in line or "test_timing_db.json" in line)
        ]
        self.assertEqual(
            dirty_lines,
            [],
            f"Test runner dirtied tracked receipt/timing files in worktree: {dirty_lines}",
        )

    def test_full_log_path_is_absolute_resolution(self):
        """full_log_path in stage failure diagnostics must resolve to exact absolute path on disk."""
        from tools.pr_governance_core.diagnostics import format_failed_stage_diagnostics

        diag = format_failed_stage_diagnostics(
            stage_idx=99,
            stage_name="Unit Verification Stage",
            cmd=["pytest", "tests/foo.py"],
            output="Traceback (most recent call last):\n  File 'test.py', line 1\nFAILED tests/foo.py::test_bar",
            repo_root=ROOT,
            branch_slug="test_slug",
        )

        full_log_path = diag.get("full_log_path")
        self.assertTrue(full_log_path, "Diagnostics must produce full_log_path")
        p = Path(full_log_path)
        self.assertTrue(p.is_absolute(), f"full_log_path must be absolute, got: {full_log_path}")
        self.assertTrue(p.exists(), f"Log file must physically exist at {full_log_path}")
        content = p.read_text(encoding="utf-8")
        self.assertIn("FAILED tests/foo.py::test_bar", content)

    def test_prompt_ssot_sync_mirroring(self):
        """sync_prompt_exports must deterministically prune retired duplicate scheduled_tasks_hardened exports."""
        from tools.lint_prompt_ssot import TASK_EXPORT_MAP, sync_prompt_exports

        repo = self.tmp_dir / "mock_repo"
        repo.mkdir()
        prompts_dir = repo / "automations" / "prompts"
        prompts_dir.mkdir(parents=True)
        exports_dir = repo / "artifacts" / "exports" / "scheduled_tasks_hardened"
        exports_dir.mkdir(parents=True)

        canon_name = "pr_queue_observer_agent.md"
        export_name = TASK_EXPORT_MAP[canon_name]

        canon_file = prompts_dir / canon_name
        export_file = exports_dir / export_name

        test_payload = "# Updated PR Queue Observer Prompt v4.0.0\nTesting prompt SSOT.\n"
        canon_file.write_text(test_payload, encoding="utf-8")
        export_file.write_text("# Outdated duplicate export\n", encoding="utf-8")

        removed = sync_prompt_exports(repo)
        self.assertIn(f"retired duplicate export: {export_name}", removed)
        self.assertFalse(export_file.exists())


if __name__ == "__main__":
    unittest.main()
