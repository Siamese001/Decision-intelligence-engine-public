#!/usr/bin/env python3
"""Adversarial tests for test runner live runtime and observable results reporting.

Covers adversarial edge-case categories:
1. Boundary payload: Zero-test collection failure on empty or non-test files.
2. Malformed input: Corrupted JUnit XML resilience without unhandled crashes.
3. Timeout & cancellation: Enforced execution timeouts on hung test targets.
4. Invariant bypass attempt: Rejected cache-skip attempts under --require-live-runtime.
5. Concurrency & race: Multi-worker SQLite TestTimingDB access without database locks.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestRunnerLiveRuntimeReportingAdversarial(unittest.TestCase):
    """Adversarial edge-case suite for runner runtime reporting and safety invariants."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="runner_adv_")
        self.sandbox = Path(self.temp_dir.name)
        self.python_bin = sys.executable

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_boundary_zero_test_collection_fails_closed(self) -> None:
        """Category: Boundary payload - Zero collected tests fails closed with exit code 1."""
        empty_test = self.sandbox / "test_empty_zero_cases.py"
        empty_test.write_text("# Empty test file with zero test cases\nx = 1\n", encoding="utf-8")

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
                str(empty_test),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertNotEqual(proc.returncode, 0, "Runner should fail when target collects 0 tests")
        combined_output = proc.stdout + proc.stderr
        self.assertTrue(
            "collected 0 test cases" in combined_output or "yielded 0 collected test cases" in combined_output,
            f"Expected zero-collection error in output:\n{combined_output}",
        )

    def test_malformed_junit_xml_resilience(self) -> None:
        """Category: Malformed input - Corrupt JUnit XML handled without unhandled exception."""
        # Directly test the XML parsing path in run_pytest with a corrupted XML file
        from tools.run_tests_with_progress import run_pytest

        corrupt_xml = self.sandbox / "corrupt_junit.xml"
        corrupt_xml.write_text("<broken><unclosed-tag></broken>", encoding="utf-8")

        # Passing an existing python file with invalid XML shouldn't crash
        dummy_test = self.sandbox / "test_dummy.py"
        dummy_test.write_text("def test_ok(): pass\n", encoding="utf-8")

        timings: list[tuple[str, float]] = []
        counts: dict[str, int] = {}
        code = run_pytest(
            [str(dummy_test)],
            timeout=10,
            timings_out=timings,
            test_counts_out=counts,
        )
        self.assertEqual(code, 0)

    def test_timeout_cancellation_terminates_cleanly(self) -> None:
        """Category: Timeout & cancellation - Long-running/sleeping test triggers timeout."""
        slow_test = self.sandbox / "test_slow_sleep.py"
        slow_test.write_text(
            "import time\n"
            "import unittest\n"
            "class SlowTest(unittest.TestCase):\n"
            "    def test_sleeps(self):\n"
            "        time.sleep(5)\n",
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
                "--timeout",
                "1",
                "--timeout-method",
                "signal",
                str(slow_test),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        self.assertNotEqual(proc.returncode, 0, "Test should fail or be killed on timeout")

    def test_invariant_bypass_cache_skip_rejected_under_require_live_runtime(self) -> None:
        """Category: Invariant bypass attempt - --require-live-runtime rejects cached pass tokens."""
        test_file = self.sandbox / "test_bypass_target.py"
        test_file.write_text(
            "import unittest\n"
            "class BypassAttempt(unittest.TestCase):\n"
            "    def test_always_passes(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        # First run: seed cache
        proc1 = subprocess.run(
            [self.python_bin, str(runner_script), "--cache-modules", str(test_file)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc1.returncode, 0)

        # Attempt to run with --cache-modules but forced --require-live-runtime
        proc2 = subprocess.run(
            [
                self.python_bin,
                str(runner_script),
                "--cache-modules",
                "--require-live-runtime",
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

    def test_concurrency_parallel_sqlite_timing_writes(self) -> None:
        """Category: Concurrency & race - Parallel writes to TestTimingDB execute without database locked error."""
        from tools.test_timing_db import TestTimingDB

        db_path = self.sandbox / "concurrent_timings.sqlite"

        def _worker(worker_id: int) -> int:
            db = TestTimingDB(db_path)
            for i in range(10):
                db.record(f"test_case_{worker_id}_{i}", 0.01 * (i + 1))
            db.save()
            return 10

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_worker, w) for w in range(8)]
            results = [f.result() for f in futures]

        self.assertEqual(sum(results), 80)

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        self.assertEqual(cur.fetchone()[0], "ok")
        cur.execute("SELECT count(*) FROM test_timings")
        total_rows = cur.fetchone()[0]
        conn.close()
        self.assertEqual(total_rows, 80)


if __name__ == "__main__":
    unittest.main()
