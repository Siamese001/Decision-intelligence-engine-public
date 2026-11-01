"""tests/test_universal_pipeline_subfolder_governance.py

Comprehensive tests for Universal Subfolder Architecture across all pipelines going forward,
standardized high-signal meeting/document naming, and non-retrospective historical preservation.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.meeting_paths import (
    MeetingPathEnvelope,
    standardize_meeting_slug,
)
from tools.validate_output_hierarchy import (
    validate_meeting_directory,
)


class TestUniversalPipelineSubfolderGovernance(unittest.TestCase):
    """Test suite ensuring universal subfolder architecture and non-retrospective compatibility."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_universal_subfolder_"))
        self.output_root = self.temp_dir / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_standardize_meeting_slug_invariants(self) -> None:
        """Verifies standardized slug rules: <= 40 chars, no boilerplate, high-signal encoding."""
        # 1. Boilerplate removal and attendee encoding
        slug1 = standardize_meeting_slug(
            "discuss_Project_Lens_SOW_Boundaries",
            attendees=["harper Hoffman", "Jen Pan", "Amit Ayer"],
        )
        self.assertEqual(slug1, "SOW_Boundaries_Mandy_PartnerAdvisory")
        self.assertLessEqual(len(slug1), 40)

        # 2. Document title formatting and word boundary capping
        slug2 = standardize_meeting_slug(
            "Coming together as one Technology organization",
            max_len=40,
        )
        self.assertLessEqual(len(slug2), 40)
        self.assertFalse(slug2.endswith("_"))

        # 3. Phase design standardization
        slug3 = standardize_meeting_slug(
            "Review LENS Designs Phase 1 Completion",
            attendees=["Jen Pan", "Amit Ayer"],
        )
        self.assertEqual(slug3, "Phase1_Designs_Jen_Amit")

    def test_document_pipeline_universal_subfolder_routing(self) -> None:
        """Verifies DocumentPipeline creates dedicated subfolders and isolates receipts."""
        doc_content = (
            "From: Petter Skantze <leader@company.com>\n"
            "Date: 09/16/2026\n"
            "Subject: Enterprise Cloud Architecture Modernization\n\n"
            "Executive directive on cloud modernization and technology governance.\n"
        )
        doc_file = self.temp_dir / "cloud_memo.txt"
        doc_file.write_text(doc_content, encoding="utf-8")

        pipeline = DocumentPipeline(provider_type="offline")
        res = pipeline.run(
            str(doc_file),
            output_dir=str(self.output_root),
            metadata={"project": "enterprise_architecture", "date": "09/16/2026"},
        )

        self.assertEqual(res["status"], "COMPLETE")
        dest_p = Path(res["output_dir"])

        # Must be nested: output/<project>/<date>/<subfolder>
        self.assertEqual(dest_p.parent.name, "09-16-2026")
        self.assertEqual(dest_p.parent.parent.name, "enterprise_architecture")
        self.assertLessEqual(len(dest_p.name), 40)

        # Deliverable 03 and all 4 receipts must reside in dest_p
        self.assertTrue((dest_p / "03_Enterprise_Cloud_Architecture_09-16-2026_canonical_signal_projection.json").exists()
                        or len(list(dest_p.glob("03_*_canonical_signal_projection.json"))) == 1)
        self.assertTrue((dest_p / "qa_gates_report.json").exists())
        self.assertTrue((dest_p / "llm_judge_evaluation.json").exists())
        self.assertTrue((dest_p / "final_release_decision.json").exists())
        self.assertTrue((dest_p / "intake_package.json").exists())

        # Zero loose files at date root
        date_dir = dest_p.parent
        self.assertEqual(len(list(date_dir.glob("03_*.json"))), 0)
        self.assertEqual(len(list(date_dir.glob("*.json"))), 0)

        # Hierarchy validation passes on both date folder and subfolder directly
        self.assertEqual(validate_meeting_directory(dest_p), [])
        self.assertEqual(validate_meeting_directory(date_dir), [])

    def test_historical_non_retrospective_preservation(self) -> None:
        """Verifies historical single-meeting dates without subfolders validate cleanly."""
        hist_date = self.output_root / "project_nexus" / "10-02-2026"
        hist_date.mkdir(parents=True, exist_ok=True)
        stem = "Nexus_Strategy_10-02-2026_1000AM_1100AM_ET"

        (hist_date / f"01_{stem}_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")
        (hist_date / f"02_{stem}_executive_recap.md").write_text("# Recap", encoding="utf-8")
        (hist_date / f"03_{stem}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (hist_date / "qa_gates_report.json").write_text("{}", encoding="utf-8")
        (hist_date / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
        (hist_date / "final_release_decision.json").write_text("{}", encoding="utf-8")
        (hist_date / "intake_package.json").write_text("{}", encoding="utf-8")

        # Must pass without requiring retrospective subfoldering
        violations = validate_meeting_directory(hist_date, project_name="project_nexus")
        self.assertEqual(violations, [])

    def test_meeting_envelope_subfolder_containment_across_projects(self) -> None:
        """Verifies MeetingPathEnvelope isolates all outputs inside subfolder for various projects."""
        test_cases = [
            ("project_lens", "09-15-2026", "Review LENS Designs Phase 1 Completion", ["Jen Pan", "Amit Ayer"]),
            ("nea", "09-16-2026", "NEA CI Prep Kannan Debrief", ["Kannan", "PartnerAdvisory Team"]),
            ("project_atlas", "09-17-2026", "Rewire Architecture Alignment", ["PartnerAdvisory Team"]),
        ]

        for proj, dt, name, atts in test_cases:
            slug = standardize_meeting_slug(name, attendees=atts, max_len=40)
            env = MeetingPathEnvelope.resolve(
                project_name=proj,
                meeting_date=dt,
                meeting_name=name,
                meeting_time="1000AM_1100AM_ET",
                repo_root=self.temp_dir,
                subfolder=slug,
                attendees=atts,
                standardize_name=True,
            )

            expected_dir = self.output_root / proj / dt / slug
            self.assertEqual(env.output_dir, expected_dir)
            self.assertEqual(env.deliverable_01.parent, expected_dir)
            self.assertEqual(env.deliverable_02.parent, expected_dir)
            self.assertEqual(env.deliverable_03.parent, expected_dir)
            self.assertEqual(env.qa_report.parent, expected_dir)
            self.assertEqual(env.judge_evaluation.parent, expected_dir)
            self.assertEqual(env.release_decision.parent, expected_dir)
            self.assertEqual(env.intake_package.parent, expected_dir)
            self.assertLessEqual(len(slug), 40)


if __name__ == "__main__":
    unittest.main()
