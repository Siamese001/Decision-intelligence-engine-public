"""End-to-End Governance and Lifecycle Suite for Test Timeout Preemption.

Validates that:
1. run_tests_with_progress.py handles timed-out pytest tests cleanly without hanging.
2. run_tests_with_progress.py enforces signal timeout preemption on POSIX.
3. Process hygiene is preserved with zero orphaned or zombie worker processes.
4. Wave gate runner integrates signal preemption into receipt generation.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.e2e
class TimeoutGovernanceE2ETests(unittest.TestCase):
    """End-to-end tests for test runner timeout preemption and process cleanup."""

    def setUp(self) -> None:
        self.runner_script = ROOT / "tools" / "run_tests_with_progress.py"
        self.assertTrue(self.runner_script.is_file(), "run_tests_with_progress.py must exist")

    def test_runner_preempts_pytest_and_cleans_up(self) -> None:
        """Lifecycle: Progress runner terminates a hung pytest test and exits cleanly with error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test_timeout_lifecycle.py"
            test_file.write_text(
                "import time\n\n"
                "def test_passing_item():\n"
                "    assert True\n\n"
                "def test_hung_item():\n"
                "    while True:\n"
                "        time.sleep(0.05)\n",
                encoding="utf-8",
            )

            cmd = [
                sys.executable,
                str(self.runner_script),
                "--runner",
                "pytest",
                "--timeout=2",
                str(test_file),
            ]
            t0 = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            duration = time.monotonic() - t0

            self.assertNotEqual(proc.returncode, 0, "Runner must exit with non-zero status on timeout")
            self.assertLess(
                duration, 8.0, f"Progress runner must terminate hung test within deadline, took {duration:.2f}s"
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertTrue(
                "Timeout" in combined or "timed out" in combined.lower() or "failed" in combined.lower(),
                f"Runner output must record failure: {combined[-500:]}",
            )

    def test_runner_passes_fast_tests_cleanly_with_signal_header(self) -> None:
        """Lifecycle: Passing test suite reports signal timeout method in pytest session header."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test_fast_lifecycle.py"
            test_file.write_text(
                "def test_quick():\n"
                "    assert 1 + 1 == 2\n",
                encoding="utf-8",
            )

            cmd = [
                sys.executable,
                str(self.runner_script),
                "--runner",
                "pytest",
                "-v",
                "--timeout=10",
                str(test_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, f"Passing test must exit with 0: {proc.stderr}")

            combined = proc.stdout or ""
            if os.name == "posix":
                self.assertIn(
                    "timeout method: signal",
                    combined,
                    "Pytest session header on POSIX must declare 'timeout method: signal'",
                )

    def test_process_hygiene_no_dangling_workers_after_timeout(self) -> None:
        """Process Hygiene: Verifies no orphaned python workers remain after timeout preemption."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test_orphan_probe.py"
            test_file.write_text(
                "import time\n"
                "def test_spin():\n"
                "    while True:\n"
                "        time.sleep(0.05)\n",
                encoding="utf-8",
            )

            cmd = [
                sys.executable,
                str(self.runner_script),
                "--runner",
                "pytest",
                "--timeout=2",
                str(test_file),
            ]
            subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))

            # Check process table for any test_orphan_probe worker processes
            ps_proc = subprocess.run(["ps", "aux"], capture_output=True, text=True)
            output = ps_proc.stdout or ""
            self.assertNotIn(
                "test_orphan_probe.py",
                output,
                "No dangling Python processes running the timed-out test file should remain in process table",
            )

    def test_wave_gate_runner_enforces_signal_preemption_arg(self) -> None:
        """Integration: run_wave_gate._run_single_test_suite dispatches with signal timeout method."""
        gate_script = ROOT / "tools" / "run_wave_gate.py"
        self.assertTrue(gate_script.is_file(), "run_wave_gate.py must exist")

        content = gate_script.read_text(encoding="utf-8")
        self.assertIn(
            "--timeout-method=signal",
            content,
            "run_wave_gate.py must explicitly pass --timeout-method=signal on POSIX platforms",
        )


if __name__ == "__main__":
    unittest.main()
