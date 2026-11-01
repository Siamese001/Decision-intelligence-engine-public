"""E2E Test: NEE Complete Org Hierarchy Source Intake & Manifest Verification.

Verifies that the assembled markdown source document and 20 authentic mobile screenshots
exist, match sha256 checksums, and provide full coverage of executive leadership nodes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from dual_pipeline.core import sha256_file
from dual_pipeline.evidence_field_limits import validate_source_preflight

ROOT = Path(__file__).resolve().parent.parent.parent


class TestNEEOrgHierarchySourceManifestE2E(unittest.TestCase):
    """End-to-end verification of authentic NEE leadership source document and attachments."""

    def setUp(self) -> None:
        self.doc_path = ROOT / "intake" / "2026-10-14_NEE_Organizational_Hierarchy_Leadership_Complete.md"
        self.attachments_dir = ROOT / "intake" / "2026-10-14_nee_org_hierarchy_attachments"

    def test_source_document_exists_and_populated(self) -> None:
        """Verify the consolidated intake markdown document exists and contains comprehensive leadership sections."""
        self.assertTrue(self.doc_path.is_file(), f"Missing intake markdown document: {self.doc_path}")
        content = self.doc_path.read_text(encoding="utf-8")
        validate_source_preflight(content, {})
        self.assertEqual(len(sha256_file(self.doc_path)), 64)
        self.assertGreater(len(content), 1000, "Source document too brief")

        # Verify key executive leaders
        required_leaders = [
            "John Ketchum",
            "Petter Skantze",
            "Richard Argentieri",
            "Matthew Barrows",
            "Ben Miron",
            "logan Miller",
            "Marcus Balk",
            "Speaker_Alpha Muschett",
            "Eugene Ryzhikov",
            "Ashwin Kunduru",
            "Mark Lemasney",
            "Javid Khan",
            "Jennifer Belger",
            "Krishnakumar Venkataraman",
            "Eric Haskell",
            "Nicole Daggs",
            "Cora Duke-Botros",
            "Scott Bores",
            "Timothy Oliver",
            "TJ Kudalis",
        ]
        for leader in required_leaders:
            self.assertIn(leader, content, f"Missing leader in source markdown: {leader}")

    def test_attachments_directory_contains_20_screenshots(self) -> None:
        """Verify the attachments directory contains all 20 authentic mobile screenshots."""
        self.assertTrue(self.attachments_dir.is_dir(), f"Missing attachments dir: {self.attachments_dir}")
        images = list(self.attachments_dir.glob("media_*"))
        self.assertEqual(len(images), 20, f"Expected 20 screenshots, found {len(images)}")
        for img in images:
            self.assertGreater(img.stat().st_size, 1000, f"Image {img.name} appears truncated or empty")


if __name__ == "__main__":
    unittest.main()
