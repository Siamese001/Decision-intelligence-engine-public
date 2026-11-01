"""
tests/adversarial/test_directory_compliance_isolation_adversarial.py

Adversarial test suite covering edge cases for scoped directory compliance,
sibling isolation, and staging lifecycle.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.release_runner import promote_staging_to_release
from tools.validate_output_hierarchy import validate_meeting_directory


class TestDirectoryComplianceIsolationAdversarial(unittest.TestCase):
    """Adversarial stress tests for directory compliance and staging isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "output"
        self.project_dir = self.output_dir / "gemini_enablement"
        self.date_dir = self.project_dir / "09-30-2026"
        self.date_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_meeting_structure(self, container: Path, stem: str) -> None:
        container.mkdir(parents=True, exist_ok=True)
        (container / f"01_{stem}_hitl_resolved_transcript.md").write_text("# T", encoding="utf-8")
        (container / f"02_{stem}_executive_recap.md").write_text("# R", encoding="utf-8")
        (container / f"03_{stem}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (container / "qa_gates_report.json").write_text("{}", encoding="utf-8")
        (container / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
        (container / "final_release_decision.json").write_text("{}", encoding="utf-8")
        (container / "intake_package.json").write_text("{}", encoding="utf-8")
        (container / "metadata.json").write_text("{}", encoding="utf-8")

    def test_adversarial_oversized_and_boilerplate_target_slugs_fail(self) -> None:
        """Adversarial edge case: Target slug with boilerplate prefix or >40 chars fails validation."""
        # 1. Boilerplate prefix
        bp_sub = self.date_dir / "discuss_Working_Files"
        self._create_meeting_structure(bp_sub, "discuss_Working_Files_09-30-2026_1000AM_1030AM_ET")
        violations = validate_meeting_directory(bp_sub, project_name="gemini_enablement")
        self.assertTrue(any("[BOILERPLATE_MEETING_NAME]" in v for v in violations))

        # 2. Oversized slug (>40 chars)
        oversized_slug = "This_Is_An_Extremely_Long_Meeting_Slug_That_Exceeds_Forty_Characters"
        self.assertGreater(len(oversized_slug), 40)
        os_sub = self.date_dir / oversized_slug
        self._create_meeting_structure(os_sub, f"{oversized_slug}_09-30-2026_1000AM_1030AM_ET")
        violations = validate_meeting_directory(os_sub, project_name="gemini_enablement")
        self.assertTrue(any("[OVERSIZED_MEETING_NAME]" in v for v in violations))

        # 3. Prohibited draft/temp slug
        draft_sub = self.date_dir / "Working_Files_draft"
        self._create_meeting_structure(draft_sub, "Working_Files_draft_09-30-2026_1000AM_1030AM_ET")
        violations = validate_meeting_directory(draft_sub, project_name="gemini_enablement")
        self.assertTrue(any("[PROHIBITED_DRAFT_DIRECTORY]" in v for v in violations))

    def test_adversarial_concurrent_staging_directories_isolation(self) -> None:
        """Adversarial edge case: Concurrent staging directories do not interfere or cross-contaminate."""
        stage1 = self.date_dir / ".staging_session_alpha_1111"
        stage2 = self.date_dir / ".staging_session_beta_2222"
        stage1.mkdir(parents=True, exist_ok=True)
        stage2.mkdir(parents=True, exist_ok=True)

        (stage1 / "file1.txt").write_text("alpha", encoding="utf-8")
        (stage2 / "file2.txt").write_text("beta", encoding="utf-8")

        target1 = self.date_dir / "Session_Alpha"
        promote_staging_to_release(stage1, target1, purge_staging=True)

        # Stage1 is purged, but Stage2 is intact and uncorrupted
        self.assertFalse(stage1.exists())
        self.assertTrue(stage2.exists())
        self.assertTrue((target1 / "file1.txt").exists())
        self.assertEqual((stage2 / "file2.txt").read_text(encoding="utf-8"), "beta")

    def test_adversarial_nonexistent_target_slug_handling(self) -> None:
        """Adversarial edge case: Specifying a nonexistent target_slug reports MISSING_TARGET_SUBFOLDER."""
        sub = self.date_dir / "Actual_Meeting"
        self._create_meeting_structure(sub, "Actual_Meeting_09-30-2026_1000AM_1030AM_ET")

        violations = validate_meeting_directory(
            self.date_dir, project_name="gemini_enablement", target_slug="Ghost_Meeting_Not_Found"
        )
        self.assertTrue(any("[MISSING_TARGET_SUBFOLDER]" in v for v in violations))

    def test_adversarial_promote_staging_exception_hygiene(self) -> None:
        """Adversarial edge case: Staging directory is cleaned up in finally block even on error."""
        stage = self.date_dir / ".staging_error_test"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "data.txt").write_text("data", encoding="utf-8")

        # Invalid destination path that raises OSError
        invalid_target = Path("/dev/null/forbidden_path_test")

        with self.assertRaises(OSError):
            promote_staging_to_release(stage, invalid_target, purge_staging=True)

        # Despite exception, stage was purged in finally block
        self.assertFalse(stage.exists())


if __name__ == "__main__":
    unittest.main()
