"""
tests/end_to_end/test_pr_gate_copilot_smoke_e2e.py

End-to-End verification of Wave 4: Black-Box Copilot Smoke Gate in PR Main Readiness.
Verifies that Gate 31 executes against the authoritative active knowledge graph,
validating that `nee copilot` evaluates offline graph queries and outputs grounded citations,
authorizing local main readiness promotion.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from tools.verify_copilot_smoke import ROOT, verify_copilot_active_graph_smoke
from tools.verify_main_readiness import verify_main_readiness


class TestPRGateCopilotSmokeE2E(unittest.TestCase):
    """End-to-end lifecycle verification for PR Gate 31 copilot smoke gate."""

    def test_verify_copilot_active_graph_smoke_direct(self) -> None:
        """Verifies that verify_copilot_active_graph_smoke succeeds against active graph."""
        success, message = verify_copilot_active_graph_smoke(repo_root=ROOT)
        self.assertTrue(success, f"Copilot smoke test failed: {message}")
        self.assertIn("Copilot active graph smoke test passed", message)

    def test_verify_main_readiness_gate_31_dispatch(self) -> None:
        """Verifies verify_main_readiness(gate=31) dispatches Gate 31 and returns exit code 0."""
        exit_code = verify_main_readiness(gate=31)
        self.assertEqual(exit_code, 0, "Expected gate 31 execution to return exit code 0")

    def test_verify_main_readiness_cli_gate_31_subprocess(self) -> None:
        """Verifies CLI execution of tools/verify_main_readiness.py --gate 31 via subprocess."""
        cmd = [sys.executable, "-B", "tools/verify_main_readiness.py", "--gate", "31"]
        env = os.environ.copy()
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI gate 31 failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("Gate 31", proc.stdout)
        self.assertIn("✅ PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
