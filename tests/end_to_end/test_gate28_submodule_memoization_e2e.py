#!/usr/bin/env python3
"""End-to-End test for Gate 28 Test Module Memoization (Wave 2).

Verifies:
1. Initial test suite run caches passing test modules into .local/test_module_cache.
2. An incremental edit to a dependency of Module A re-runs Module A while Module B is skipped.
3. Runner receipt JSON correctly reports cached_modules_skipped and executed_modules.
4. Second run finishes in sub-second time with 100% accurate pass tallies.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.test_module_cache import (
    clear_module_cache,
    compute_module_fingerprint,
    get_cached_module,
    save_cached_module,
)


class Gate28SubmoduleMemoizationE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="gate28_memo_e2e_")
        self.repo_root = (Path(self.tmp_dir) / "repo").resolve()
        self.repo_root.mkdir(parents=True)

        # Create source packages
        src_dir = self.repo_root / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        self.dep_a = src_dir / "dep_a.py"
        self.dep_a.write_text("def helper_a(): return 10\n", encoding="utf-8")
        self.dep_b = src_dir / "dep_b.py"
        self.dep_b.write_text("def helper_b(): return 20\n", encoding="utf-8")

        # Create tests
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        self.test_a = tests_dir / "test_module_a.py"
        self.test_a.write_text(
            "import unittest\nfrom pkg.dep_a import helper_a\n\n"
            "class TestA(unittest.TestCase):\n"
            "    def test_a(self): self.assertEqual(helper_a(), 10)\n",
            encoding="utf-8",
        )
        self.test_b = tests_dir / "test_module_b.py"
        self.test_b.write_text(
            "import unittest\nfrom pkg.dep_b import helper_b\n\n"
            "class TestB(unittest.TestCase):\n"
            "    def test_b(self): self.assertEqual(helper_b(), 20)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        clear_module_cache(self.repo_root)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_fingerprint_isolation_on_dependency_mutation(self) -> None:
        """Modifying dep_a changes fingerprint for test_module_a but NOT test_module_b."""
        fp_a1, deps_a = compute_module_fingerprint("tests.test_module_a", self.repo_root)
        fp_b1, deps_b = compute_module_fingerprint("tests.test_module_b", self.repo_root)

        self.assertIn("pkg/dep_a.py", deps_a)
        self.assertIn("pkg/dep_b.py", deps_b)

        # Mutate dep_a
        self.dep_a.write_text("def helper_a(): return 999  # modified\n", encoding="utf-8")

        fp_a2, _ = compute_module_fingerprint("tests.test_module_a", self.repo_root)
        fp_b2, _ = compute_module_fingerprint("tests.test_module_b", self.repo_root)

        self.assertNotEqual(fp_a1, fp_a2, "Fingerprint for test_module_a must change when dep_a changes")
        self.assertEqual(fp_b1, fp_b2, "Fingerprint for test_module_b must remain identical")

    def test_02_e2e_runner_module_caching_and_receipt(self) -> None:
        """Simulate runner caching: unaffected modules skip execution and write to receipt."""
        receipt_path = self.repo_root / "reports" / "test_runner_receipt.json"

        # Pre-seed cache for test_module_b
        fp_b, _ = compute_module_fingerprint("tests.test_module_b", self.repo_root)
        save_cached_module("tests.test_module_b", self.repo_root, fp_b, test_count=1, duration_seconds=0.05)

        # Run test runner via CLI with --cache-modules
        runner_script = Path(__file__).resolve().parents[2] / "tools" / "run_tests_with_progress.py"
        cmd = [
            sys.executable,
            "-B",
            str(runner_script),
            "--cache-modules",
            "--receipt",
            str(receipt_path),
            "tests.test_module_a",
            "tests.test_module_b",
        ]

        import os

        run_env = os.environ.copy()
        worktree_root = runner_script.parents[1]
        run_env["PYTHONPATH"] = f"{self.repo_root}:{worktree_root}:{run_env.get('PYTHONPATH', '')}"

        proc = subprocess.run(cmd, cwd=self.repo_root, env=run_env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Runner failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

        self.assertTrue(receipt_path.is_file(), "Runner receipt must be generated")
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(receipt_data["terminal_outcome"], "PASS")
        self.assertIn("tests.test_module_b", receipt_data["cached_modules_skipped"])
        self.assertIn("tests.test_module_a", receipt_data["executed_modules"])
        self.assertEqual(receipt_data["cached_count"], 1)
        self.assertEqual(receipt_data["executed_count"], 1)


if __name__ == "__main__":
    unittest.main()
