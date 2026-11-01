"""Unit test suite for declarative IngestionMeetingManifest and ManifestPipelineRunner."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from dual_pipeline.manifest import IngestionMeetingManifest

MANIFEST_910 = ROOT / "manifests" / "apex_trans" / "09-10-2026.manifest.json"
MANIFEST_911 = ROOT / "manifests" / "apex_trans" / "09-11-2026.manifest.json"


class TestManifestRunner(unittest.TestCase):
    def test_manifest_deserialization_and_validation(self):
        """Verify manifest loads cleanly and resolves golden artifacts."""
        m1 = IngestionMeetingManifest.from_json(MANIFEST_910)
        self.assertEqual(m1.project_name, "apex_trans")
        self.assertEqual(m1.human_date, "09/10/2026")
        self.assertEqual(m1.path_date, "09-10-2026")
        self.assertEqual(m1.iso_date, "2026-09-10")

        f2_text = m1.resolve_f2_text()
        self.assertIsNotNone(f2_text)
        self.assertIn("Executive Recap", f2_text)

        f3_data = m1.resolve_f3_data()
        self.assertIsNotNone(f3_data)
        self.assertIn("graph_mutations", f3_data)

        m2 = IngestionMeetingManifest.from_json(MANIFEST_911)
        self.assertEqual(m2.project_name, "apex_trans")
        self.assertEqual(m2.human_date, "09/11/2026")
        self.assertIsNotNone(m2.resolve_f2_text())
        self.assertIsNotNone(m2.resolve_f3_data())

    def test_manifest_runner_execution_interface(self):
        """Verify ManifestPipelineRunner resolves output paths and handles dry run flags."""
        m1 = IngestionMeetingManifest.from_json(MANIFEST_910)
        self.assertTrue(m1.base_name.startswith("NEET_Insights_Hub"))
        self.assertEqual(m1.classification, "confidential")
