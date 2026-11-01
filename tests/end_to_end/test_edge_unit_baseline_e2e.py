"""
tests/end_to_end/test_edge_unit_baseline_e2e.py

End-to-end test suite exercising the full CLI lifecycle of tools/audit_edge_unit_baseline.py.
Verifies CLI invocation, exit codes, JSON output formatting, and sealed receipt persistence.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from tests.test_edge_unit_baseline import create_mock_runtime

ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_SCRIPT = ROOT / "tools" / "audit_edge_unit_baseline.py"


class TestEdgeUnitBaselineE2E(unittest.TestCase):
    """End-to-end testing of baseline audit CLI execution."""

    def test_cli_execution_mock_runtime_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=False)
            receipt_out = Path(tmp_dir) / "output_receipt.json"

            cmd = [
                sys.executable,
                str(AUDIT_SCRIPT),
                "--runtime-root", info["runtime_root"],
                "--output", str(receipt_out),
                "--json",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")

            # Parse JSON from stdout
            stdout_data = json.loads(proc.stdout)
            self.assertEqual(stdout_data["status"], "PASS")
            self.assertTrue(stdout_data["hash_verification"]["graph_matches_pin"])
            self.assertTrue(stdout_data["hash_verification"]["zero_mutation_verified"])
            self.assertIn("canonical_digest", stdout_data)

            # Check written receipt file
            self.assertTrue(receipt_out.is_file())
            file_data = read_json(receipt_out)
            self.assertEqual(file_data["canonical_digest"], stdout_data["canonical_digest"])
            self.assertEqual(file_data["status"], "PASS")

    def test_cli_execution_live_runtime_e2e(self) -> None:
        live_root = ROOT / ".local" / "dual_pipeline"
        if not (live_root / "active_pair.json").is_file():
            self.skipTest(".local/dual_pipeline/active_pair.json not present")

        with tempfile.TemporaryDirectory() as tmp_dir:
            receipt_out = Path(tmp_dir) / "live_receipt.json"

            cmd = [
                sys.executable,
                str(AUDIT_SCRIPT),
                "--runtime-root", str(live_root),
                "--output", str(receipt_out),
                "--json",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")

            stdout_data = json.loads(proc.stdout)
            self.assertEqual(stdout_data["status"], "PIN_DRIFT")
            self.assertFalse(stdout_data["hash_verification"]["graph_matches_pin"])
            self.assertTrue(stdout_data["hash_verification"]["sidecar_matches_pin"])
            self.assertTrue(stdout_data["hash_verification"]["zero_mutation_verified"])

            # Verify ground truth counts from live graph
            am = stdout_data["assertion_metrics"]
            self.assertEqual(am["total_assertions"], 2579)
            self.assertEqual(am["current_unknown_assertions"], 2252)
            self.assertEqual(am["edge_shaped_assertions"], 1078)
            self.assertEqual(am["literal_shaped_assertions"], 1174)
            self.assertEqual(am["legacy_edge_backfill_count"], 907)
            self.assertEqual(am["edge_shaped_with_quote"], 937)

            nm = stdout_data["node_metrics"]
            self.assertEqual(nm["total_nodes"], 799)

            sm = stdout_data["sidecar_metrics"]
            self.assertEqual(sm["total_records"], 1282)
            self.assertEqual(sm["edge_like_sidecar_count"], 640)

            # Verify saved file
            self.assertTrue(receipt_out.is_file())
            file_data = read_json(receipt_out)
            self.assertEqual(file_data["canonical_digest"], stdout_data["canonical_digest"])


if __name__ == "__main__":
    unittest.main()
