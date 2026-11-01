#!/usr/bin/env python3
"""End-to-end tests for test runner live runtime and observable results reporting.

Verifies that tools/run_tests_with_progress.py executes tests live, forbids module
cache skipping under --require-live-runtime, captures per-test JUnit XML timings
from pytest into TestTimingDB, and renders the in-band Runtime & Observable Results
Breakdown table.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestRunnerLiveRuntimeReportingE2E(unittest.TestCase):
    """E2E suite verifying live runtime execution and observable results reporting."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="runner_e2e_")
        self.sandbox = Path(self.temp_dir.name)
        self.python_bin = sys.executable

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unittest_runner_produces_runtime_breakdown_and_persists_timings(self) -> None:
        """Verify unittest suites render the runtime table and persist timings."""
        test_file = self.sandbox / "test_sample_unit.py"
        test_file.write_text(
            "import unittest\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_pass_one(self):\n"
            "        self.assertTrue(True)\n"
            "    def test_pass_two(self):\n"
            "        self.assertEqual(1 + 1, 2)\n",
            encoding="utf-8",
        )

        runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        proc = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--require-live-runtime",
                "--timeout",
                "60",
                str(test_file),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, f"Runner failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertIn("Test Execution Runtime & Observable Results Breakdown", proc.stdout)
        self.assertIn("Latency Distribution: P50=", proc.stdout)
        self.assertIn("Slowest Tests:", proc.stdout)
        self.assertIn("Test Execution Summary: [PASS]", proc.stdout)

        # Verify TestTimingDB recorded the test methods
        sqlite_db = ROOT / ".local" / "test_timings.sqlite"
        self.assertTrue(sqlite_db.exists(), "test_timings.sqlite was not created")
        conn = sqlite3.connect(f"file:{sqlite_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT test_id, last_duration_s FROM test_timings WHERE test_id LIKE '%SampleTest%'")
        rows = cur.fetchall()
        conn.close()
        self.assertGreaterEqual(len(rows), 2, f"Expected >= 2 test timing records, found: {rows}")

    def test_pytest_runner_produces_runtime_breakdown_and_persists_timings(self) -> None:
        """Verify pytest suites parse JUnit XML timings and render the runtime table."""
        test_file = self.sandbox / "test_sample_pytest.py"
        test_file.write_text(
            "def test_pytest_alpha():\n"
            "    assert 42 == 42\n"
            "def test_pytest_beta():\n"
            "    assert 'hello'.upper() == 'HELLO'\n",
            encoding="utf-8",
        )

        runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        proc = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--runner",
                "pytest",
                "--require-live-runtime",
                "--timeout",
                "60",
                str(test_file),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, f"Pytest runner failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertIn("Test Execution Runtime & Observable Results Breakdown", proc.stdout)
        self.assertIn("Latency Distribution: P50=", proc.stdout)
        self.assertIn("Test Execution Summary: [PASS]", proc.stdout)

        sqlite_db = ROOT / ".local" / "test_timings.sqlite"
        conn = sqlite3.connect(f"file:{sqlite_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT test_id, last_duration_s FROM test_timings WHERE test_id LIKE '%test_pytest_%'")
        rows = cur.fetchall()
        conn.close()
        self.assertGreaterEqual(len(rows), 2, f"Expected pytest test timings recorded in SQLite, found: {rows}")

    def test_require_live_runtime_bypasses_module_cache(self) -> None:
        """Verify --require-live-runtime bypasses warm module cache and executes live."""
        test_file = self.sandbox / "test_cache_target.py"
        test_file.write_text(
            "import unittest\n"
            "class CacheBypassTest(unittest.TestCase):\n"
            "    def test_executed(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        # Run 1: with cache
        proc1 = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--cache-modules",
                "--timeout",
                "60",
                str(test_file),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc1.returncode, 0)

        # Run 2: with require-live-runtime even if cache-modules passed
        proc2 = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--cache-modules",
                "--require-live-runtime",
                "--timeout",
                "60",
                str(test_file),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc2.returncode, 0)
        self.assertIn("Live Runtime Required: Bypassing module cache", proc2.stdout)
        self.assertNotIn("all from cache", proc2.stdout)

    def test_mixed_unittest_and_pytest_targets_unified(self) -> None:
        """Verify auto-routing handles mixed unittest and pytest targets into unified table."""
        u_file = self.sandbox / "test_mixed_u.py"
        u_file.write_text(
            "import unittest\n"
            "class UTest(unittest.TestCase):\n"
            "    def test_u(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        p_file = self.sandbox / "test_mixed_p.py"
        p_file.write_text(
            "def test_p():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        proc = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--require-live-runtime",
                "--timeout",
                "60",
                str(u_file),
                str(p_file),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Total Modules: 2 (Executed: 2, Cached: 0)", proc.stdout)
        self.assertIn("Test Execution Summary: [PASS]", proc.stdout)

    def test_independent_sqlite_readback_integrity(self) -> None:
        """Verify fresh read-only SQLite connection reads back recorded timings and valid schema."""
        sqlite_db = ROOT / ".local" / "test_timings.sqlite"
        self.assertTrue(sqlite_db.exists())
        conn = sqlite3.connect(f"file:{sqlite_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        status = cur.fetchone()[0]
        self.assertEqual(status, "ok")

        cur.execute("SELECT count(*), max(last_duration_s), min(last_duration_s) FROM test_timings")
        count, max_d, min_d = cur.fetchone()
        conn.close()
        self.assertGreater(count, 0)
        self.assertGreaterEqual(max_d, 0.0)
        self.assertGreaterEqual(min_d, 0.0)


if __name__ == "__main__":
    unittest.main()
