"""End-to-end lifecycle test for semantic unit and reachability audit."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, digest, read_json


class TestReachabilityAuditE2E(unittest.TestCase):
    def setUp(self):
        self.runtime_root = ROOT / ".local" / "dual_pipeline"
        self.active_pair_path = self.runtime_root / "active_pair.json"
        if not self.active_pair_path.is_file():
            self.skipTest("Active runtime pair not found, skipping live E2E")

    def test_e2e_cli_semantic_unit_audit_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_receipt = Path(tmp_dir) / "audit_receipt.json"

            cmd = [
                sys.executable,
                "-m",
                "dual_pipeline.cli",
                "semantic-unit-audit",
                "--runtime-root",
                str(self.runtime_root),
                "--output-receipt",
                str(out_receipt),
                "--json",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"CLI command failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

            # Verify receipt file created
            self.assertTrue(out_receipt.is_file(), "Audit receipt file was not created")
            receipt = read_json(out_receipt)

            # 1. Structural schema compliance
            self.assertEqual(receipt.get("audit_kind"), "semantic_units_and_reachability")
            self.assertEqual(receipt.get("schema_version"), "1.0.0")
            self.assertIn("receipt_digest", receipt)

            # 2. Cryptographic digest verification
            saved_digest = receipt["receipt_digest"]
            payload_copy = dict(receipt)
            payload_copy.pop("receipt_digest", None)
            expected_digest = digest(payload_copy)
            self.assertEqual(saved_digest, expected_digest, "Receipt digest is corrupted or invalid")

            # 3. Grounded baseline characterization
            summary = receipt.get("summary", {})
            self.assertIn(summary.get("total_current_assertions"), {2252, 2273})
            self.assertIn(summary.get("directly_vectorized_assertions"), {825, 834})
            self.assertIn(summary.get("reachable_direct_count"), {825, 834})
            self.assertEqual(summary.get("unreachable_unexplained_count"), 0)
            self.assertGreaterEqual(summary.get("total_reachable_count", 0), 1900)

            # 4. Unit type geometry & tie rate verification
            units = receipt.get("unit_type_inventory", {})
            expected_types = {"action_item", "architecture_relationship", "assertion", "edge", "entity_facet"}
            self.assertTrue(expected_types.issubset(set(units.keys())))

            edge_metrics = units.get("edge", {})
            self.assertGreater(edge_metrics.get("near_duplicate_share", 0.0), 0.20)
            self.assertGreater(edge_metrics.get("margin_tie_rate", 0.0), 0.90)

    def test_e2e_cli_formatted_text_output(self):
        cmd = [
            sys.executable,
            "-m",
            "dual_pipeline.cli",
            "semantic-unit-audit",
            "--runtime-root",
            str(self.runtime_root),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI formatted command failed: {proc.stderr}")
        self.assertIn("# Semantic Unit & Reachability Audit Report", proc.stdout)
        self.assertIn("ZERO GAP", proc.stdout)
        self.assertIn("Total Current Assertions", proc.stdout)


if __name__ == "__main__":
    unittest.main()
