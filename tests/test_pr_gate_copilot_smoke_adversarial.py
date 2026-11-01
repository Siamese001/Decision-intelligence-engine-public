"""
tests/test_pr_gate_copilot_smoke_adversarial.py

Adversarial and boundary edge-case test suite for Wave 4 PR Gate 31 Copilot Smoke Gate.
Covers at least 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty stdout, whitespace-only subprocess return.
2. Malformed input / Crash: Subprocess non-zero exit codes, subprocess timeout exceptions.
3. Invariant bypass: Subprocess output omitting grounded citations, session discovery errors.
4. Exact production regression: Out-of-bounds gate indices in verify_main_readiness.
"""
from __future__ import annotations

import subprocess
import types
import unittest

from tools.verify_copilot_smoke import ROOT, verify_copilot_active_graph_smoke
from tools.verify_main_readiness import verify_main_readiness


class TestPRGateCopilotSmokeAdversarial(unittest.TestCase):
    """Adversarial and boundary test cases for Gate 31 smoke validation."""

    def test_boundary_empty_copilot_stdout(self) -> None:
        """Category 1: Boundary payload - Empty or whitespace-only stdout fails closed."""
        def dummy_runner(*_args, **_kwargs):
            return types.SimpleNamespace(returncode=0, stdout="   \n\t  ", stderr="")

        success, message = verify_copilot_active_graph_smoke(ROOT, runner=dummy_runner)
        self.assertFalse(success)
        self.assertIn("empty stdout", message)

    def test_malformed_subprocess_nonzero_exit_code(self) -> None:
        """Category 2: Malformed input / Crash - Non-zero return code fails closed."""
        def dummy_runner(*_args, **_kwargs):
            return types.SimpleNamespace(returncode=2, stdout="", stderr="SyntaxError: invalid syntax")

        success, message = verify_copilot_active_graph_smoke(ROOT, runner=dummy_runner)
        self.assertFalse(success)
        self.assertIn("exited with code 2", message)

    def test_malformed_subprocess_timeout_exception(self) -> None:
        """Category 2: Malformed input / Crash - TimeoutExpired exception fails closed."""
        def dummy_runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["nee.py"], timeout=60)

        success, message = verify_copilot_active_graph_smoke(ROOT, runner=dummy_runner)
        self.assertFalse(success)
        self.assertIn("Failed executing copilot subprocess", message)

    def test_invariant_bypass_session_discovery_error(self) -> None:
        """Category 3: Invariant bypass - Session discovery error in stdout fails closed."""
        def dummy_runner(*_args, **_kwargs):
            return types.SimpleNamespace(
                returncode=0,
                stdout="Error: NO_LIVE_CONTEXT. No active recording session found for project_lens.",
                stderr="",
            )

        success, message = verify_copilot_active_graph_smoke(ROOT, runner=dummy_runner)
        self.assertFalse(success)
        self.assertIn("session discovery error", message)

    def test_invariant_bypass_ungrounded_output_without_citations(self) -> None:
        """Category 3: Invariant bypass - Output without grounded citations fails closed."""
        def dummy_runner(*_args, **_kwargs):
            return types.SimpleNamespace(
                returncode=0,
                stdout="General status: Everything looks fine across all teams.",
                stderr="",
            )

        success, message = verify_copilot_active_graph_smoke(ROOT, runner=dummy_runner)
        self.assertFalse(success)
        self.assertIn("lacked grounded citations or commitments", message)

    def test_exact_regression_invalid_gate_index_fails_closed(self) -> None:
        """Category 4: Exact regression - Out-of-range gate index fails closed with exit code 1."""
        exit_code_high = verify_main_readiness(gate=999)
        self.assertEqual(exit_code_high, 1, "Expected gate 999 to fail closed with exit code 1")

        exit_code_zero = verify_main_readiness(gate=0)
        self.assertEqual(exit_code_zero, 1, "Expected gate 0 to fail closed with exit code 1")


if __name__ == "__main__":
    unittest.main()
