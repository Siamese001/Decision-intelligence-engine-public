"""E2E Test: NEE Complete Org Hierarchy Output 03 & Deliverable Verification.

Validates that the Document Pipeline generated Deliverable 03 canonical signal projection,
suppressed Outputs 01 and 02, and sealed the complete intake package.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from dual_pipeline.core import read_json, sha256_file
from dual_pipeline.evidence_field_limits import validate_projection_field_lengths

ROOT = Path(__file__).resolve().parent.parent.parent


class TestNEEOrgHierarchyOutput3E2E(unittest.TestCase):
    """End-to-end verification of authentic NEE leadership Output 03 deliverables."""

    def setUp(self) -> None:
        self.deliverable_dir = ROOT / "output" / "enterprise_architecture" / "10-14-2026" / "apex_global_Energy_Executive"

    def test_output_dir_and_required_receipts_exist(self) -> None:
        """Verify the output container directory exists with all canonical receipts."""
        self.assertTrue(self.deliverable_dir.is_dir(), f"Missing output dir: {self.deliverable_dir}")

        required_files = [
            "final_release_decision.json",
            "graph_mutation_breakdown.md",
            "intake_package.json",
            "llm_judge_evaluation.json",
            "metadata.json",
            "pipeline_execution_receipt.json",
            "qa_gates_report.json",
            "source_manifest.json",
        ]
        for rf in required_files:
            target = self.deliverable_dir / rf
            self.assertTrue(target.is_file(), f"Missing {rf} in {self.deliverable_dir}")

    def test_output_01_and_02_suppressed(self) -> None:
        """Verify meeting transcripts (01) and executive recaps (02) are cleanly suppressed."""
        out1_files = list(self.deliverable_dir.glob("01_*"))
        out2_files = list(self.deliverable_dir.glob("02_*"))
        self.assertEqual(len(out1_files), 0, f"Expected 0 Output 01 files, found: {out1_files}")
        self.assertEqual(len(out2_files), 0, f"Expected 0 Output 02 files, found: {out2_files}")

    def test_output_03_canonical_signal_projection_complete(self) -> None:
        """Verify Deliverable 03 signal projection exists, is valid JSON, and has passing judge score."""
        proj_files = list(self.deliverable_dir.glob("03_*_canonical_signal_projection.json"))
        self.assertEqual(len(proj_files), 1, f"Expected exactly 1 signal projection in {self.deliverable_dir}")

        data = read_json(proj_files[0])
        validate_projection_field_lengths(data)
        self.assertEqual(len(sha256_file(proj_files[0])), 64)
        mutations = data.get("graph_mutations", {})
        self.assertIn("entity_upserts", mutations)
        self.assertIn("relationship_signals", mutations)
        self.assertGreater(len(mutations["entity_upserts"]), 0)
        self.assertGreater(len(mutations["relationship_signals"]), 0)

        decision = read_json(self.deliverable_dir / "final_release_decision.json")
        self.assertEqual(decision.get("status"), "PASS", f"Final release decision was not PASS: {decision}")
        self.assertTrue(decision.get("final_pass"), f"final_pass was not true: {decision}")


if __name__ == "__main__":
    unittest.main()
