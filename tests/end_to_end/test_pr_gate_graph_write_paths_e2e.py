"""tests/end_to_end/test_pr_gate_graph_write_paths_e2e.py

End-to-End verification of Gate 32: Direct Graph Write Path Linter in PR Main Readiness.
Verifies that Gate 32 executes across the repository tree and via verify_main_readiness.py,
validating zero unapproved graph mutations exist.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_graph_write_paths import lint_graph_write_paths
from tools.verify_main_readiness import verify_main_readiness


class TestPRGateGraphWritePathsE2E(unittest.TestCase):
    """End-to-end lifecycle verification for PR Gate 32 direct graph write path linter."""

    def test_verify_gate_32_direct(self) -> None:
        """Verifies that lint_graph_write_paths passes cleanly on repository root."""
        exit_code, violations = lint_graph_write_paths(repo_root=ROOT)
        self.assertEqual(exit_code, 0, f"Gate 32 failed with violations: {violations}")
        self.assertEqual(len(violations), 0)

    def test_verify_main_readiness_gate_32_dispatch(self) -> None:
        """Verifies verify_main_readiness(gate=32) dispatches Gate 32 and returns exit code 0."""
        exit_code = verify_main_readiness(gate=32)
        self.assertEqual(exit_code, 0, "Expected gate 32 execution to return exit code 0")

    def test_verify_main_readiness_cli_gate_32_subprocess(self) -> None:
        """Verifies CLI execution of tools/verify_main_readiness.py --gate 32 via subprocess."""
        cmd = [sys.executable, "-B", "tools/verify_main_readiness.py", "--gate", "32"]
        env = os.environ.copy()
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI gate 32 failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("Gate 32", proc.stdout)
        self.assertIn("✅ PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
