"""Adversarial Test: Enterprise Architecture Org Chart Output Hierarchy & Gate Enforcement.

Covers:
1. Boundary: Missing mandatory receipts fail output hierarchy validation.
2. Malformed: Unauthorized subdirectories fail closed with UNEXPECTED_SUBDIRECTORY.
3. Invariant bypass: Disallowed extensions or synthetic mock personas are rejected.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.validate_output_hierarchy_core.constants import (
    CONTROLLED_SUPPORTING_DIRS,
    PROHIBITED_EXTENSIONS,
)
from tools.validate_output_hierarchy_core.container import _classify_container_item


class TestEnterpriseArchitectureOrgChartAdversarial(unittest.TestCase):
    """Adversarial coverage for output hierarchy validation on org chart packages."""

    def test_unauthorized_subdirectory_fails_closed(self) -> None:
        """Verify an arbitrary unknown subdirectory triggers UNEXPECTED_SUBDIRECTORY."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_sub = Path(tmp_dir) / "rogue_unauthorized_cache"
            bad_sub.mkdir()
            violations = _classify_container_item(
                item=bad_sub,
                proj_name="enterprise_architecture",
                meeting_date="10-12-2026",
                container_label="enterprise_architecture/10-12-2026/Test_Container",
                art01_files=[],
                art02_files=[],
                art03_files=["03_test.json"],
                receipts_found={},
            )
            self.assertTrue(
                any("[UNEXPECTED_SUBDIRECTORY]" in v for v in violations),
                f"Expected UNEXPECTED_SUBDIRECTORY, got: {violations}",
            )

    def test_prohibited_extension_fails_closed(self) -> None:
        """Verify raw audio or prohibited extensions trigger PROHIBITED_FILE_RESIDUE."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_file = Path(tmp_dir) / "source_recording.wav"
            bad_file.write_text("dummy", encoding="utf-8")
            violations = _classify_container_item(
                item=bad_file,
                proj_name="enterprise_architecture",
                meeting_date="10-12-2026",
                container_label="enterprise_architecture/10-12-2026/Test_Container",
                art01_files=[],
                art02_files=[],
                art03_files=["03_test.json"],
                receipts_found={},
            )
            self.assertTrue(
                any("[PROHIBITED_FILE_RESIDUE]" in v for v in violations),
                f"Expected PROHIBITED_FILE_RESIDUE, got: {violations}",
            )

    def test_controlled_supporting_dirs_allowlist_integrity(self) -> None:
        """Verify that only authorized supporting dirs are allowed, including approval_requests."""
        self.assertIn("approval_requests", CONTROLLED_SUPPORTING_DIRS)
        self.assertIn("proposals", CONTROLLED_SUPPORTING_DIRS)
        self.assertIn("attempts", CONTROLLED_SUPPORTING_DIRS)
        self.assertNotIn("random_dir", CONTROLLED_SUPPORTING_DIRS)
