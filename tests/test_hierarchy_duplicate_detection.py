"""
tests/test_hierarchy_duplicate_detection.py

Unit tests verifying duplicate meeting collision detection in tools/validate_output_hierarchy.py.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.validate_output_hierarchy import validate_meeting_directory


class TestHierarchyDuplicateDetection(unittest.TestCase):
    """Certifies that validate_output_hierarchy audits sibling subfolders for duplicate collisions."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_meeting_subfolder(
        self,
        subfolder: Path,
        stem: str,
        meeting_date: str,
        time_str: str,
        attendees_md: str,
        discourse_text: str,
        allow_duplicate: bool = False,
        override_rationale: str | None = None,
    ) -> None:
        subfolder.mkdir(parents=True, exist_ok=True)
        (subfolder / f"01_{stem}_hitl_resolved_transcript.md").write_text(
            f"# Header\n**Date:** {meeting_date.replace('-', '/')}\n**Time:** {time_str}\n"
            f"**Attendees:**\n{attendees_md}\n\n## Transcript\n{discourse_text}\n",
            encoding="utf-8",
        )
        (subfolder / f"02_{stem}_executive_recap.md").write_text(
            f"# Header\n**Date:** {meeting_date.replace('-', '/')}\n**Time:** {time_str}\n"
            f"**Attendees:**\n{attendees_md}\n\n## Meeting Objective\n{discourse_text}\n",
            encoding="utf-8",
        )
        (subfolder / f"03_{stem}_canonical_signal_projection.json").write_text(
            json.dumps({"schema_version": "5.7", "status": "PASS"}),
            encoding="utf-8",
        )
        (subfolder / "qa_gates_report.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (subfolder / "llm_judge_evaluation.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (subfolder / "final_release_decision.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        (subfolder / "intake_package.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

        if allow_duplicate:
            meta = {
                "project": "project_nexus",
                "date": meeting_date,
                "allow_duplicate": True,
                "collision_override_rationale": override_rationale or "Authorized",
            }
            (subfolder / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def test_duplicate_sibling_subfolders_trigger_violation(self) -> None:
        """Verify that duplicate sibling subfolders on the same date are flagged."""
        date_dir = self.output_dir / "project_nexus" / "10-02-2026"
        sub1 = date_dir / "Nexus_Scope"
        sub2 = date_dir / "Nexus_Reid_NEA"

        shared_discourse = (
            "Reid Schenk and Amit Ayer discussed labor hour validation with Dan and Sharon. "
            "Taylor Mercer noted $12 million in capital savings across 40 sites. "
            "Matt Martinello and Raisa Weiser handle financial endorsements in the app. "
            "Chris Go/No-Go funding review on Wednesday."
        )
        attendees = (
            "- **PartnerAdvisory:** Taylor Mercer, Quinn Mercer, Amit Ayer\n"
            "- **Apex Global Enterprises:** Reid Schenk"
        )

        self._create_meeting_subfolder(
            sub1, "Nexus_Scope_10-02-2026_1018AM_1030AM_ET", "10-02-2026",
            time_str="10:15 AM - 10:30 AM ET", attendees_md=attendees, discourse_text=shared_discourse,
        )
        self._create_meeting_subfolder(
            sub2, "Nexus_Reid_NEA_10-02-2026_1000AM_1015AM_ET", "10-02-2026",
            time_str="10:00 AM - 10:15 AM ET", attendees_md=attendees, discourse_text=shared_discourse,
        )

        violations = validate_meeting_directory(date_dir, project_name="project_nexus")
        self.assertTrue(any("[UNRESOLVED_DUPLICATE_MEETING_PAIR]" in v for v in violations))
        self.assertTrue(any("Nexus_Scope" in v and "Nexus_Reid_NEA" in v for v in violations))

    def test_authorized_override_bypasses_duplicate_violation(self) -> None:
        """Verify that documented override in metadata.json bypasses the collision violation."""
        date_dir = self.output_dir / "project_nexus" / "10-02-2026"
        sub1 = date_dir / "Nexus_Scope"
        sub2 = date_dir / "Nexus_Reid_NEA"

        shared_discourse = (
            "Reid Schenk and Amit Ayer discussed labor hour validation with Dan and Sharon. "
            "Taylor Mercer noted $12 million in capital savings across 40 sites. "
            "Matt Martinello and Raisa Weiser handle financial endorsements in the app."
        )
        attendees = "- **Attendees:** Reid Schenk, Amit Ayer"

        self._create_meeting_subfolder(
            sub1, "Nexus_Scope_10-02-2026_1018AM_1030AM_ET", "10-02-2026",
            time_str="10:15 AM - 10:30 AM ET", attendees_md=attendees, discourse_text=shared_discourse,
        )
        self._create_meeting_subfolder(
            sub2, "Nexus_Reid_NEA_10-02-2026_1000AM_1015AM_ET", "10-02-2026",
            time_str="10:00 AM - 10:15 AM ET", attendees_md=attendees, discourse_text=shared_discourse,
            allow_duplicate=True, override_rationale="Separate concurrent breakout track",
        )

        violations = validate_meeting_directory(date_dir, project_name="project_nexus")
        self.assertFalse(any("[UNRESOLVED_DUPLICATE_MEETING_PAIR]" in v for v in violations))

    def test_distinct_meetings_pass_without_violation(self) -> None:
        """Verify that distinct meetings on the same date pass with 0 violations."""
        date_dir = self.output_dir / "project_lens" / "09-15-2026"
        sub1 = date_dir / "Phase1_Designs_Jen_Amit"
        sub2 = date_dir / "SOW_Boundaries_Mandy_PartnerAdvisory"

        disc_1 = (
            "Jen Pan and Amit Ayer reviewed AWS infrastructure architecture, event-driven lambdas, "
            "and PostgreSQL database sizing for Phase 1 Project Beacon telemetry pipeline."
        )
        att_1 = "- **Attendees:** Jen Pan, Amit Ayer"

        disc_2 = (
            "Mandy and PartnerAdvisory reviewed commercial SOW boundaries, billing rates, "
            "milestone acceptance schedules, and legal governance agreements."
        )
        att_2 = "- **Attendees:** Mandy, PartnerAdvisory Lead"

        self._create_meeting_subfolder(
            sub1, "Phase1_Designs_Jen_Amit_09-15-2026_0232PM_0300PM_ET", "09-15-2026",
            time_str="02:30 PM - 03:00 PM ET", attendees_md=att_1, discourse_text=disc_1,
        )
        self._create_meeting_subfolder(
            sub2, "SOW_Boundaries_Mandy_PartnerAdvisory_09-15-2026_0430PM_0505PM_ET", "09-15-2026",
            time_str="04:30 PM - 05:00 PM ET", attendees_md=att_2, discourse_text=disc_2,
        )

        violations = validate_meeting_directory(date_dir, project_name="project_lens")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
