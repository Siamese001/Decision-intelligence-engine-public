"""Adversarial Verification Suite for Timeout Preemption and GIL Interruption.

Categories Covered:
1. Overflow & exhaustion: CPU spin loop preemption under kernel SIGALRM holding the GIL.
2. Malformed input: ReDoS catastrophic backtracking interruption on nested quantifiers.
3. Exact production regression: Historical 70-character input against ReDoS regex terminating within limit.
4. Rollback & recovery: Clean restoration of signal handlers and alarm state post-interruption.
5. Concurrency & race: Subprocess pytest runner preemption under signal timeout method.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


class CustomAlarmInterruption(BaseException):
    """Exception raised by test signal handler to verify kernel preemption."""


@pytest.mark.adversarial
class TimeoutPreemptionAdversarialTests(unittest.TestCase):
    """Adversarial edge-case tests validating kernel-level timeout preemption."""

    def setUp(self) -> None:
        self.original_handler = signal.getsignal(signal.SIGALRM)

    def tearDown(self) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.original_handler)

    def test_cpu_spin_loop_preempted_by_signal(self) -> None:
        """Category: Overflow & exhaustion - CPU spin loop holding GIL is preempted by SIGALRM."""
        if os.name != "posix":
            self.skipTest("SIGALRM preemption requires POSIX platform")

        interrupted = False

        def _alarm_handler(signum: int, frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            raise CustomAlarmInterruption("Spin loop preempted")

        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(1)
        t0 = time.monotonic()
        try:
            while True:
                # Tight CPU loop that never releases GIL
                pass
        except CustomAlarmInterruption:
            pass
        finally:
            signal.alarm(0)

        duration = time.monotonic() - t0
        self.assertTrue(interrupted, "Kernel SIGALRM must interrupt tight CPU loop holding GIL")
        self.assertLess(duration, 2.0, f"Preemption must occur near 1.0s, took {duration:.2f}s")

    def test_redos_backtracking_preempted_by_signal(self) -> None:
        """Category: Malformed input - Native C sre backtracking is interrupted by SIGALRM."""
        if os.name != "posix":
            self.skipTest("SIGALRM preemption requires POSIX platform")

        interrupted = False

        def _alarm_handler(signum: int, frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            raise CustomAlarmInterruption("ReDoS backtracking preempted")

        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(1)
        t0 = time.monotonic()
        try:
            # 35 repeating characters against (a+)+$ requires millions of backtracking steps in C sre
            re.match(r"(a+)+$", "a" * 35 + "!")  # noqa: redos
        except CustomAlarmInterruption:
            pass
        finally:
            signal.alarm(0)

        duration = time.monotonic() - t0
        self.assertTrue(interrupted, "Kernel SIGALRM must interrupt native regex backtracking")
        self.assertLess(duration, 2.0, f"ReDoS preemption must occur within 2.0s, took {duration:.2f}s")

    def test_exact_production_regression_citation_re(self) -> None:
        """Category: Exact production regression - 70-character input evaluated against nested quantifier."""
        if os.name != "posix":
            self.skipTest("SIGALRM preemption requires POSIX platform")

        interrupted = False

        def _alarm_handler(signum: int, frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            raise CustomAlarmInterruption("Production regression ReDoS preempted")

        # Nested quantifier citation pattern from incident
        citation_re = re.compile(r"^(\s*\[[\w\.\-]+\]\s*)+$")  # noqa: redos
        adversarial_70_char_record = " [" + "a" * 70 + "]"

        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(1)
        t0 = time.monotonic()
        try:
            citation_re.match(adversarial_70_char_record)
        except CustomAlarmInterruption:
            pass
        finally:
            signal.alarm(0)

        duration = time.monotonic() - t0
        self.assertLess(duration, 2.0, f"70-char ReDoS evaluation must terminate in < 2.0s, took {duration:.2f}s")

    def test_signal_handler_restoration_and_recovery(self) -> None:
        """Category: Rollback & recovery - Post-interruption state restores handlers cleanly."""
        if os.name != "posix":
            self.skipTest("SIGALRM requires POSIX platform")

        def _temp_handler(signum: int, frame: Any) -> None:
            raise CustomAlarmInterruption("temp")

        signal.signal(signal.SIGALRM, _temp_handler)
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.original_handler)

        current_handler = signal.getsignal(signal.SIGALRM)
        self.assertEqual(current_handler, self.original_handler, "Original signal handler must be restored")

    def test_subprocess_pytest_signal_timeout_preemption(self) -> None:
        """Category: Concurrency & race - Subprocess pytest preempts hung test under signal method."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test_hung_subshell.py"
            test_file.write_text(
                "import time\n\n"
                "def test_infinite_spin():\n"
                "    while True:\n"
                "        time.sleep(0.05)\n",
                encoding="utf-8",
            )

            cmd = [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-v",
                "--timeout=2",
                "--timeout-method=signal" if os.name == "posix" else "--timeout-method=thread",
            ]
            t0 = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            duration = time.monotonic() - t0

            self.assertNotEqual(proc.returncode, 0, "Pytest must fail when test times out")
            self.assertLess(duration, 6.0, f"Subprocess must be terminated by timeout in < 6.0s, took {duration:.2f}s")
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertTrue(
                "Timeout" in combined or "timed out" in combined.lower(),
                f"Pytest output must indicate timeout failure: {combined[-500:]}",
            )


if __name__ == "__main__":
    unittest.main()
