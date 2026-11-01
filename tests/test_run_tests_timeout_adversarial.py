"""
Adversarial: run_tests_with_progress --timeout must actually stop the suite.

Regression pin for the 27-minute hang observed under `--timeout 60`: the SIGALRM handler raised a
plain BaseException that unittest's catch-all recorded as one test error, the one-shot alarm was
spent, and every remaining test kept running with no limit.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_tests_with_progress.py"

HANGING_MODULE = textwrap.dedent(
    """
    import time, unittest

    class Hang(unittest.TestCase):
        def test_a_hangs(self):
            time.sleep(120)

        def test_b_must_not_run(self):
            print("SENTINEL_B_EXECUTED", flush=True)

    class Swallow(unittest.TestCase):
        def test_swallows_interrupts_forever(self):
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    time.sleep(120)
                except BaseException:
                    pass
    """
)


class RunnerTimeoutAdversarial(unittest.TestCase):
    def _run(self, test_id: str, timeout: int, grace: int) -> tuple[subprocess.CompletedProcess, float]:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "prq_hang_fixture.py").write_text(HANGING_MODULE, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=f"{d}{os.pathsep}{ROOT}", NEE_TEST_TIMEOUT_GRACE_S=str(grace))
            t0 = time.monotonic()
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--timeout", str(timeout), test_id],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
            )
            return proc, time.monotonic() - t0

    def test_timeout_stops_suite_and_skips_remaining_tests(self) -> None:
        proc, elapsed = self._run("prq_hang_fixture.Hang", timeout=2, grace=20)
        out = proc.stdout + proc.stderr
        self.assertLess(elapsed, 20, f"suite ran {elapsed:.1f}s despite --timeout 2")
        self.assertIn("timed out after 2s", out)
        self.assertNotIn("SENTINEL_B_EXECUTED", out, "tests after the timeout must not execute")
        self.assertNotEqual(proc.returncode, 0)

    def test_watchdog_kills_suite_that_swallows_interrupts(self) -> None:
        proc, elapsed = self._run("prq_hang_fixture.Swallow", timeout=2, grace=3)
        self.assertLess(elapsed, 30, f"watchdog did not fire ({elapsed:.1f}s)")
        self.assertEqual(proc.returncode, 124, proc.stdout[-500:] + proc.stderr[-500:])
        self.assertIn("FATAL: test suite exceeded --timeout", proc.stderr)


if __name__ == "__main__":
    unittest.main()
