"""E2E Test: Enterprise Architecture & Leadership Org Chart Deliverables.

Validates that all ingested Enterprise Architecture and Apex Global Analytics org chart packages
conform to canonical signal projection schemas, deliverable hierarchy standards, and
contain authentic entity and reporting relationships.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent.parent


class TestEnterpriseArchitectureOrgChartE2E(unittest.TestCase):
    """End-to-end verification of authentic NEE Enterprise Architecture org chart deliverables."""

    def setUp(self) -> None:
        self.ea_root = ROOT / "output" / "enterprise_architecture" / "10-12-2026"
        self.nea_root = ROOT / "output" / "nea" / "10-12-2026"

    def test_enterprise_architecture_packages_exist_and_complete(self) -> None:
        """Verify all 5 enterprise architecture output packages exist with complete deliverables."""
        expected_ea_dirs = [
            "Florida_Power_Light_Executive",
            "apex_global_Energy_Executive_Leadership_Ai_S",
            "apex_global_Energy_It_Technology_Executive",
            "apex_global_Energy_Power_Executive",
            "apex_global_Energy_Sap_Executive",
        ]
        self.assertTrue(self.ea_root.is_dir(), f"Missing directory: {self.ea_root}")
        for pkg in expected_ea_dirs:
            pkg_dir = self.ea_root / pkg
            self.assertTrue(pkg_dir.is_dir(), f"Missing EA package: {pkg_dir}")

            # Verify required receipts and deliverables
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
                target = pkg_dir / rf
                self.assertTrue(target.is_file(), f"Missing {rf} in {pkg_dir}")

            proj_files = list(pkg_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(proj_files), 1, f"Expected exactly 1 signal projection in {pkg_dir}")

    def test_nea_energy_analytics_package_complete(self) -> None:
        """Verify Apex Global Analytics Energy Executive package is complete and passing."""
        nea_pkg = self.nea_root / "apex_global_Analytics_Energy_Executive"
        self.assertTrue(nea_pkg.is_dir(), f"Missing NEA package: {nea_pkg}")

        decision_file = nea_pkg / "final_release_decision.json"
        self.assertTrue(decision_file.is_file())
        with open(decision_file, "r", encoding="utf-8") as f:
            decision = json.load(f)
        self.assertEqual(decision.get("status"), "PASS")
        self.assertTrue(decision.get("authoritative_write_authorized", False))

    def test_canonical_signal_projection_entities_grounding(self) -> None:
        """Verify APEX Development Technology projection contains authentic leaders and role mappings."""
        fpl_dir = self.ea_root / "Florida_Power_Light_Executive"
        proj_files = list(fpl_dir.glob("03_*_canonical_signal_projection.json"))
        self.assertEqual(len(proj_files), 1)

        with open(proj_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        entities = data.get("graph_mutations", {}).get("entity_upserts", [])
        entity_names = {e.get("name") for e in entities}
        self.assertIn("Scott Bores", entity_names)
        self.assertIn("Timothy Oliver", entity_names)
        self.assertIn("Sharon Diaram", entity_names)

    def test_approval_requests_supporting_directory_recognized(self) -> None:
        """Verify approval_requests directory is recognized by output hierarchy constants."""
        from tools.validate_output_hierarchy_core.constants import CONTROLLED_SUPPORTING_DIRS

        self.assertIn("approval_requests", CONTROLLED_SUPPORTING_DIRS)
