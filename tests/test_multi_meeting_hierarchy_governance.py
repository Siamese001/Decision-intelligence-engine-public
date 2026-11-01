"""tests/test_multi_meeting_hierarchy_governance.py

Comprehensive tests for multi-meeting subfolder governance,
standardized high-signal meeting naming, collision detection, and output hierarchy gates.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.meeting_paths import (
    MeetingPathEnvelope,
    standardize_meeting_slug,
)
from tools.validate_output_hierarchy import (
    validate_meeting_directory,
)


class TestMultiMeetingHierarchyGovernance(unittest.TestCase):
    """Test suite verifying multi-meeting subfolder isolation and naming standards."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_multi_meeting_"))
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_standardize_meeting_slug_strips_boilerplate_and_encodes_attendees(self) -> None:
        """Verifies boilerplate stripping and attendee encoding."""
        # Case 1: Review_LENS_Designs_Phase_1_Completion + Jen Pan, Amit Ayer
        slug1 = standardize_meeting_slug(
            "Review_LENS_Designs_Phase_1_Completion",
            attendees=["Jen Pan", "Amit Ayer"],
        )
        self.assertEqual(slug1, "Phase1_Designs_Jen_Amit")
        self.assertLessEqual(len(slug1), 40)

        # Case 2: discuss_LENS_SOW + harper Hoffman, Jen Pan, Alex Sanchez, Amit Ayer
        slug2 = standardize_meeting_slug(
            "discuss_LENS_SOW",
            attendees=["harper Hoffman", "Jen Pan", "Alex Sanchez", "Amit Ayer"],
        )
        self.assertEqual(slug2, "SOW_Boundaries_Mandy_PartnerAdvisory")
        self.assertLessEqual(len(slug2), 40)

        # Case 3: SOW1 Observability Catchup
        slug3 = standardize_meeting_slug(
            "Project_Lens_SOW1_Observability_Catchup",
            attendees=["harper Hoffman", "PartnerAdvisory Team"],
        )
        self.assertEqual(slug3, "SOW1_Observability_Mandy_PartnerAdvisory")
        self.assertLessEqual(len(slug3), 40)

    def test_standardize_meeting_slug_enforces_max_length(self) -> None:
        """Verifies strict adherence to maximum length limit (<= 40 chars)."""
        long_title = "Ultra_Extensive_Architecture_Diligence_And_Governance_Alignment_Review"
        attendees = ["Christopher_Alexander_Longname", "Jonathan_Bartholomew_Consultant"]
        slug = standardize_meeting_slug(long_title, attendees=attendees, max_len=40)
        self.assertLessEqual(len(slug), 40)
        self.assertFalse(slug.endswith("_"))

    def test_envelope_resolve_subfolder_containment(self) -> None:
        """Verifies MeetingPathEnvelope isolates all outputs within the meeting subfolder."""
        env = MeetingPathEnvelope.resolve(
            project_name="project_lens",
            meeting_date="09-15-2026",
            meeting_name="Phase1_Designs_Jen_Amit",
            meeting_time="02:32 PM - 03:00 PM ET",
            repo_root=self.temp_dir,
            subfolder="Phase1_Designs_Jen_Amit",
        )
        expected_dir = self.temp_dir / "output" / "project_lens" / "09-15-2026" / "Phase1_Designs_Jen_Amit"
        self.assertEqual(env.output_dir, expected_dir)
        self.assertEqual(env.deliverable_01.parent, expected_dir)
        self.assertEqual(env.deliverable_02.parent, expected_dir)
        self.assertEqual(env.deliverable_03.parent, expected_dir)
        self.assertEqual(env.qa_report.parent, expected_dir)
        self.assertEqual(env.judge_evaluation.parent, expected_dir)
        self.assertEqual(env.release_decision.parent, expected_dir)
        self.assertEqual(env.intake_package.parent, expected_dir)

    def _create_meeting_container(self, container: Path, stem: str, meeting_date: str) -> None:
        """Helper to scaffold a complete, compliant meeting directory container."""
        container.mkdir(parents=True, exist_ok=True)
        (container / f"01_{stem}_hitl_resolved_transcript.md").write_text(
            f"# Header\n**Date:** {meeting_date.replace('-', '/')}\n\n**Speaker:** [00:01] Hello",
            encoding="utf-8",
        )
        (container / f"02_{stem}_executive_recap.md").write_text(
            f"# Header\nDate: {meeting_date.replace('-', '/')}\n\nMeeting Objective\nSummary\n",
            encoding="utf-8",
        )
        (container / f"03_{stem}_canonical_signal_projection.json").write_text(
            json.dumps({"schema_version": "5.7", "status": "PASS"}),
            encoding="utf-8",
        )
        (container / "qa_gates_report.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "llm_judge_evaluation.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "final_release_decision.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (container / "intake_package.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    def test_multi_meeting_subfolders_pass_validation(self) -> None:
        """Verifies that a date directory with isolated meeting subfolders passes validation."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        sub1 = date_dir / "Phase1_Designs_Jen_Amit"
        sub2 = date_dir / "SOW_Boundaries_Mandy_PartnerAdvisory"

        self._create_meeting_container(
            sub1, "Phase1_Designs_Jen_Amit_09-15-2026_0232PM_0300PM_ET", "09-15-2026"
        )
        self._create_meeting_container(
            sub2, "SOW_Boundaries_Mandy_PartnerAdvisory_09-15-2026_0430PM_0505PM_ET", "09-15-2026"
        )

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertEqual(violations, [])

    def test_multi_meeting_root_collision_is_flagged(self) -> None:
        """Verifies that placing multiple meetings loose at the date root triggers MULTI_MEETING_ROOT_COLLISION."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        date_dir.mkdir(parents=True, exist_ok=True)

        stem1 = "MeetingA_09-15-2026_1000AM_1030AM_ET"
        stem2 = "MeetingB_09-15-2026_1400PM_1430PM_ET"

        (date_dir / f"01_{stem1}_hitl_resolved_transcript.md").write_text("dummy", encoding="utf-8")
        (date_dir / f"01_{stem2}_hitl_resolved_transcript.md").write_text("dummy", encoding="utf-8")
        (date_dir / f"02_{stem1}_executive_recap.md").write_text("dummy", encoding="utf-8")
        (date_dir / f"02_{stem2}_executive_recap.md").write_text("dummy", encoding="utf-8")
        (date_dir / f"03_{stem1}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (date_dir / f"03_{stem2}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (date_dir / "qa_gates_report.json").write_text("{}", encoding="utf-8")
        (date_dir / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
        (date_dir / "final_release_decision.json").write_text("{}", encoding="utf-8")
        (date_dir / "intake_package.json").write_text("{}", encoding="utf-8")

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertTrue(any("[MULTI_MEETING_ROOT_COLLISION]" in v for v in violations))

    def test_mixed_date_container_is_flagged(self) -> None:
        """Verifies that mixing subfolders with loose deliverable files triggers MIXED_DATE_CONTAINER."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        sub1 = date_dir / "Phase1_Designs_Jen_Amit"
        self._create_meeting_container(
            sub1, "Phase1_Designs_Jen_Amit_09-15-2026_0232PM_0300PM_ET", "09-15-2026"
        )
        # Loose file at date root
        (date_dir / "01_LooseMeeting_09-15-2026_1000AM_1030AM_ET_hitl_resolved_transcript.md").write_text(
            "dummy", encoding="utf-8"
        )

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertTrue(any("[MIXED_DATE_CONTAINER]" in v for v in violations))

    def test_subfolder_boilerplate_prefix_is_flagged(self) -> None:
        """Verifies that subfolder names starting with generic boilerplate trigger BOILERPLATE_MEETING_NAME."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        sub = date_dir / "discuss_LENS_SOW"
        self._create_meeting_container(
            sub, "discuss_LENS_SOW_09-15-2026_0430PM_0505PM_ET", "09-15-2026"
        )

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertTrue(any("[BOILERPLATE_MEETING_NAME]" in v for v in violations))

    def test_subfolder_oversized_name_is_flagged(self) -> None:
        """Verifies that subfolder names exceeding 40 characters trigger OVERSIZED_MEETING_NAME."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        long_sub_name = "Phase1_Designs_Review_And_Scope_Validation_With_Jen_And_Amit"
        self.assertGreater(len(long_sub_name), 40)
        sub = date_dir / long_sub_name
        self._create_meeting_container(
            sub, f"{long_sub_name}_09-15-2026_0232PM_0300PM_ET", "09-15-2026"
        )

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertTrue(any("[OVERSIZED_MEETING_NAME]" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
