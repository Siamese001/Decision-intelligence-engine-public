"""
tests/test_directory_compliance_isolation.py

Regression test suite verifying scoped directory compliance and sibling folder isolation.
Guarantees that an abandoned, malformed, or dirty sibling folder in a date directory
does not block publication or activation of a valid target meeting slug.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.lifecycle import check_directory_compliance
from dual_pipeline.transcript_pipeline_core.release_runner import (
    promote_staging_to_release,
    purge_stale_staging_directories,
)
from tools.validate_output_hierarchy import validate_meeting_directory
from tools.validate_output_hierarchy_core.deconfliction import detect_meeting_duplicates


class TestDirectoryComplianceIsolation(unittest.TestCase):
    """Verifies scoped directory compliance and sibling folder isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "output"
        self.project_dir = self.output_dir / "gemini_enablement"
        self.date_dir = self.project_dir / "09-30-2026"
        self.date_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_valid_meeting(self, container: Path, stem: str, meeting_date: str) -> None:
        """Helper to create a fully compliant meeting container."""
        container.mkdir(parents=True, exist_ok=True)
        (container / f"01_{stem}_hitl_resolved_transcript.md").write_text(
            f"# Forensic Transcript for {stem}\n\n[00:00:00] Speaker: Content.", encoding="utf-8"
        )
        (container / f"02_{stem}_executive_recap.md").write_text(
            f"# Executive Recap\n\nDate: {meeting_date}\n\n## Objective\nValid.", encoding="utf-8"
        )
        (container / f"03_{stem}_canonical_signal_projection.json").write_text(
            json.dumps({"schema_version": "5.7", "status": "PASS"}), encoding="utf-8"
        )
        (container / "qa_gates_report.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "llm_judge_evaluation.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "final_release_decision.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "intake_package.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "metadata.json").write_text(
            json.dumps({"project": "gemini_enablement", "date": meeting_date, "meeting_name": "Working_Files_Data"}),
            encoding="utf-8",
        )

    def test_target_slug_validates_cleanly_despite_abandoned_sibling(self) -> None:
        """A valid target meeting subfolder passes validation even if an abandoned sibling is present."""
        target_sub = self.date_dir / "Working_Files_Data_Rose_Amanda"
        stem = "Working_Files_Data_Rose_Amanda_09-30-2026_1000AM_1030AM_ET"
        self._create_valid_meeting(target_sub, stem, "09-30-2026")

        # Create an abandoned, malformed sibling folder (missing deliverables 01, 02, 03, receipts)
        abandoned_sibling = self.date_dir / "Gemini_Working_Files_Data"
        abandoned_sibling.mkdir(parents=True, exist_ok=True)
        (abandoned_sibling / "metadata.json").write_text("{}", encoding="utf-8")
        (abandoned_sibling / "scratch_notes.txt").write_text("Incomplete scratch", encoding="utf-8")

        # 1. Calling validate_meeting_directory directly on target_sub must succeed
        scoped_violations = validate_meeting_directory(target_sub, project_name="gemini_enablement")
        self.assertEqual(scoped_violations, [])

        # 2. check_directory_compliance on target_sub must succeed without raising RuntimeError
        check_directory_compliance(target_sub, proj="gemini_enablement")

        # 3. Explicitly passing target_slug to the parent date folder must also succeed
        slug_violations = validate_meeting_directory(
            self.date_dir, project_name="gemini_enablement", target_slug="Working_Files_Data_Rose_Amanda"
        )
        self.assertEqual(slug_violations, [])

        # 4. Calling unscoped validate_meeting_directory on the date folder still catches the abandoned sibling
        unscoped_violations = validate_meeting_directory(self.date_dir, project_name="gemini_enablement")
        self.assertTrue(any("[MISSING_DELIVERABLE]" in v for v in unscoped_violations))

    def test_invalid_target_slug_fails_compliance(self) -> None:
        """If the target meeting subfolder itself has violations, compliance fails closed."""
        target_sub = self.date_dir / "Working_Files_Data_Rose_Amanda"
        target_sub.mkdir(parents=True, exist_ok=True)
        # Missing Deliverable 01 and 02
        (target_sub / "03_Working_Files_Data_09-30-2026_canonical_signal_projection.json").write_text("{}", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            check_directory_compliance(target_sub, proj="gemini_enablement")
        self.assertIn("Output directory hierarchy compliance failed before publication", str(ctx.exception))

    def test_purge_stale_staging_directories(self) -> None:
        """purge_stale_staging_directories purges .staging_* and .tmp_* but preserves valid folders."""
        staging_dir = self.date_dir / ".staging_Working_Files_Data_a1b2c3d4"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "temp_file.txt").write_text("temp", encoding="utf-8")

        tmp_dir = self.date_dir / ".tmp_scratch_99"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        valid_sub = self.date_dir / "Valid_Meeting_Subfolder"
        valid_sub.mkdir(parents=True, exist_ok=True)

        purged = purge_stale_staging_directories(self.date_dir)
        self.assertEqual(len(purged), 2)
        self.assertFalse(staging_dir.exists())
        self.assertFalse(tmp_dir.exists())
        self.assertTrue(valid_sub.exists())

    def test_promote_staging_to_release_atomicity_and_cleanup(self) -> None:
        """promote_staging_to_release copies deliverables and purges staging directory in finally block."""
        staging_dir = self.date_dir / ".staging_promo_test"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "01_transcript.md").write_text("# Transcript", encoding="utf-8")
        sub_folder = staging_dir / "referenced_artifacts"
        sub_folder.mkdir(parents=True, exist_ok=True)
        (sub_folder / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        target_dir = self.date_dir / "Promoted_Meeting"

        promote_staging_to_release(staging_dir, target_dir, purge_staging=True)

        # Target directory contains promoted artifacts
        self.assertTrue((target_dir / "01_transcript.md").exists())
        self.assertTrue((target_dir / "referenced_artifacts" / "image.png").exists())
        # Staging directory was purged
        self.assertFalse(staging_dir.exists())

    def test_deconfliction_isolation_with_target_slug(self) -> None:
        """Duplicate detection ignores collisions between sibling pairs when target_slug is not involved."""
        sub_a = self.date_dir / "Phase1_Designs_Jen_Amit"
        sub_b = self.date_dir / "Phase1_Designs_Jen_Amit_Duplicate"
        target_sub = self.date_dir / "SOW_Boundaries_Mandy_PartnerAdvisory"

        stem_a = "Phase1_Designs_Jen_Amit_09-30-2026_1000AM_1030AM_ET"
        stem_b = "Phase1_Designs_Jen_Amit_Duplicate_09-30-2026_1000AM_1030AM_ET"
        stem_target = "SOW_Boundaries_Mandy_PartnerAdvisory_09-30-2026_0200PM_0230PM_ET"

        self._create_valid_meeting(sub_a, stem_a, "09-30-2026")
        self._create_valid_meeting(sub_b, stem_b, "09-30-2026")
        self._create_valid_meeting(target_sub, stem_target, "09-30-2026")

        meeting_subfolders = [sub_a, sub_b, target_sub]

        # Scoped to target_sub: ignores collision between sub_a and sub_b
        scoped_violations = detect_meeting_duplicates(
            meeting_subfolders, "gemini_enablement", "09-30-2026", self.date_dir, target_slug=target_sub.name
        )
        self.assertEqual(scoped_violations, [])


if __name__ == "__main__":
    unittest.main()
