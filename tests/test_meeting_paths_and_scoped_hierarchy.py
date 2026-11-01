"""
tests/test_meeting_paths_and_scoped_hierarchy.py

Unit tests for SSOT MeetingPathEnvelope and hermetic validate_meeting_directory().
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from dual_pipeline.meeting_paths import (
    MeetingPathEnvelope,
    normalize_meeting_date,
    normalize_time_str,
    sanitize_filename_slug,
    standardize_meeting_slug,
)
from tools.validate_output_hierarchy import validate_meeting_directory


class MeetingPathsAndScopedHierarchyTests(unittest.TestCase):
    def test_normalize_meeting_date_formats(self) -> None:
        """Tests that various valid date formats normalize to MM-DD-YYYY and MM/DD/YYYY."""
        d_dir, d_disp = normalize_meeting_date("10-02-2026")
        self.assertEqual(d_dir, "10-02-2026")
        self.assertEqual(d_disp, "09/14/2026")

        d_dir2, d_disp2 = normalize_meeting_date("09/14/2026")
        self.assertEqual(d_dir2, "10-02-2026")
        self.assertEqual(d_disp2, "09/14/2026")

        d_dir3, d_disp3 = normalize_meeting_date("2026-09-14")
        self.assertEqual(d_dir3, "10-02-2026")
        self.assertEqual(d_disp3, "09/14/2026")

        dt = datetime(2026, 9, 14, 15, 30)
        d_dir4, d_disp4 = normalize_meeting_date(dt)
        self.assertEqual(d_dir4, "10-02-2026")
        self.assertEqual(d_disp4, "09/14/2026")

        d_obj = date(2026, 9, 14)
        d_dir5, d_disp5 = normalize_meeting_date(d_obj)
        self.assertEqual(d_dir5, "10-02-2026")
        self.assertEqual(d_disp5, "09/14/2026")

    def test_normalize_meeting_date_invalid_raises(self) -> None:
        """Malformed date strings must raise ValueError."""
        with self.assertRaises(ValueError):
            normalize_meeting_date("13-40-2026")
        with self.assertRaises(ValueError):
            normalize_meeting_date("not-a-date")

    def test_sanitize_filename_slug_and_time(self) -> None:
        """Slugs and times are sanitized deterministically without consecutive underscores."""
        slug = sanitize_filename_slug("Project LENS: Phase II (Catch-up / Discussion)")
        self.assertEqual(slug, "Project_LENS_Phase_II_Catch-up_Discussion")

        time_norm = normalize_time_str("04:32 PM - 04:59 PM ET")
        self.assertEqual(time_norm, "04_32_PM_04_59_PM_ET")

    def test_envelope_resolve_unknown_project_raises(self) -> None:
        """Unknown project identifiers must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            MeetingPathEnvelope.resolve(project_name="unknown_project_xyz", meeting_date="10-02-2026")
        self.assertIn("Unknown project identifier", str(ctx.exception))

    def test_envelope_resolve_canonical_structure(self) -> None:
        """Envelope generates canonical output and isolated scratch paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_lens",
                meeting_date="10-02-2026",
                meeting_name="Phase II Catchup",
                meeting_time="0432PM_0459PM_ET",
                repo_root=root,
            )

            self.assertEqual(env.project_name, "project_lens")
            self.assertEqual(env.date_dirname, "10-02-2026")
            self.assertEqual(env.stem, "Phase_II_Catchup_10-02-2026_0432PM_0459PM_ET")
            self.assertEqual(env.output_dir, root / "output" / "project_lens" / "10-02-2026")
            self.assertEqual(env.cache_dir, root / "output" / "transcript_cache" / "project_lens" / "10-02-2026")

            # Check deliverable filenames embed MM-DD-YYYY stem
            self.assertTrue(env.deliverable_01.name.startswith("01_"))
            self.assertTrue(env.deliverable_01.name.endswith("_hitl_resolved_transcript.md"))
            self.assertIn("10-02-2026", env.deliverable_01.name)

            self.assertTrue(env.deliverable_02.name.startswith("02_"))
            self.assertTrue(env.deliverable_02.name.endswith("_executive_recap.md"))
            self.assertIn("10-02-2026", env.deliverable_02.name)

            self.assertTrue(env.deliverable_03.name.startswith("03_"))
            self.assertTrue(env.deliverable_03.name.endswith("_canonical_signal_projection.json"))
            self.assertIn("10-02-2026", env.deliverable_03.name)

            # Check scratch paths
            self.assertEqual(env.scratch_master_wav.parent, env.cache_dir)
            self.assertEqual(env.scratch_chunks_dir, env.cache_dir / ".chunks")

            # Ensure directories create successfully
            env.ensure_output_dir()
            env.ensure_cache_dir()
            self.assertTrue(env.output_dir.is_dir())
            self.assertTrue(env.cache_dir.is_dir())
            self.assertTrue(env.scratch_chunks_dir.is_dir())

    def test_validate_meeting_directory_clean_passes(self) -> None:
        """A properly formatted meeting directory passes validate_meeting_directory cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_nexus",
                meeting_date="10-02-2026",
                meeting_name="Nexus Alignment",
                meeting_time="1000AM_1100AM_ET",
                repo_root=root,
            )
            env.ensure_output_dir()

            # Create required deliverables
            env.deliverable_01.write_text("# Transcript", encoding="utf-8")
            env.deliverable_02.write_text("# Recap", encoding="utf-8")
            env.deliverable_03.write_text("{}", encoding="utf-8")

            # Create required receipts
            env.qa_report.write_text("{}", encoding="utf-8")
            env.judge_evaluation.write_text("{}", encoding="utf-8")
            env.release_decision.write_text("{}", encoding="utf-8")
            env.intake_package.write_text("{}", encoding="utf-8")

            violations = validate_meeting_directory(env.output_dir, project_name="project_nexus")
            self.assertEqual(violations, [])

    def test_validate_meeting_directory_flags_residue_and_missing_files(self) -> None:
        """validate_meeting_directory flags prohibited residue and missing receipts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_nexus",
                meeting_date="10-02-2026",
                meeting_name="Nexus Alignment",
                meeting_time="1000AM_1100AM_ET",
                repo_root=root,
            )
            env.ensure_output_dir()

            # Create deliverables but miss receipts
            env.deliverable_01.write_text("# Transcript", encoding="utf-8")
            env.deliverable_02.write_text("# Recap", encoding="utf-8")
            env.deliverable_03.write_text("{}", encoding="utf-8")

            # Inject prohibited residue
            bad_wav = env.output_dir / "recording.wav"
            bad_wav.write_bytes(b"RIFF...")
            bad_jsonl = env.output_dir / "segments.jsonl"
            bad_jsonl.write_text("{}", encoding="utf-8")

            violations = validate_meeting_directory(env.output_dir, project_name="project_nexus")
            self.assertTrue(any("[PROHIBITED_FILE_RESIDUE]" in v and "recording.wav" in v for v in violations))
            self.assertTrue(any("[PROHIBITED_FILE_RESIDUE]" in v and "segments.jsonl" in v for v in violations))
            self.assertTrue(any("[MISSING_RECEIPT]" in v for v in violations))

    def test_validate_meeting_directory_flags_hidden_residue(self) -> None:
        """Hidden files/folders other than .DS_Store trigger PROHIBITED_HIDDEN_RESIDUE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_nexus",
                meeting_date="10-02-2026",
                repo_root=root,
            )
            env.ensure_output_dir()
            # Valid deliverables & receipts
            env.deliverable_01.write_text("# 01", encoding="utf-8")
            env.deliverable_02.write_text("# 02", encoding="utf-8")
            env.deliverable_03.write_text("{}", encoding="utf-8")
            env.qa_report.write_text("{}", encoding="utf-8")
            env.judge_evaluation.write_text("{}", encoding="utf-8")
            env.release_decision.write_text("{}", encoding="utf-8")
            env.intake_package.write_text("{}", encoding="utf-8")

            # Permitted dotfile
            (env.output_dir / ".DS_Store").write_text("", encoding="utf-8")
            self.assertEqual(validate_meeting_directory(env.output_dir, project_name="project_nexus"), [])

            # Prohibited dotfile & dotfolder
            (env.output_dir / ".hidden_scratch").write_text("scratch", encoding="utf-8")
            (env.output_dir / ".chunks").mkdir()

            v = validate_meeting_directory(env.output_dir, project_name="project_nexus")
            self.assertTrue(any("[PROHIBITED_HIDDEN_RESIDUE]" in s and ".hidden_scratch" in s for s in v))
            self.assertTrue(any("[PROHIBITED_HIDDEN_RESIDUE]" in s and ".chunks" in s for s in v))

    def test_validate_meeting_directory_strict_active_recording_gate(self) -> None:
        """Active recording without allow_active_recording=True fails closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_nexus",
                meeting_date="10-02-2026",
                repo_root=root,
            )
            env.ensure_output_dir()
            live_doc = env.output_dir / "01_10-02-2026_test_live_transcript.md"
            live_doc.write_text("RECORDING_IN_PROGRESS: active stream", encoding="utf-8")

            # Fails closed by default
            v_default = validate_meeting_directory(env.output_dir, project_name="project_nexus")
            self.assertTrue(any("[ACTIVE_RECORDING_INCOMPLETE]" in s for s in v_default))

            # Passes when explicitly allowed for monitoring
            v_allowed = validate_meeting_directory(env.output_dir, project_name="project_nexus", allow_active_recording=True)
            self.assertEqual(v_allowed, [])

    def test_meeting_envelope_containment_and_manifest(self) -> None:
        """MeetingPathEnvelope includes scratch_session_manifest and enforces containment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_lens",
                meeting_date="10-02-2026",
                meeting_name="Review",
                repo_root=root,
            )
            self.assertTrue(str(env.scratch_session_manifest).endswith("_manifest.json"))
            self.assertEqual(env.scratch_session_manifest.parent, env.cache_dir)

    def test_standardize_meeting_slug(self) -> None:
        """Standardizes meeting names to concise, high-signal {Scope}_{KeyAttendees} slugs <= 40 chars."""
        # Case 1: Internal Consulting Review
        slug1 = standardize_meeting_slug(
            name="Review_LENS_Designs_Phase_1_Completion",
            attendees=["Jen Pan", "Amit Ayer"],
        )
        self.assertEqual(slug1, "Phase1_Designs_Jen_Amit")
        self.assertLessEqual(len(slug1), 40)

        # Case 2: Client + PartnerAdvisory Team
        slug2 = standardize_meeting_slug(
            name="discuss_LENS_SOW",
            attendees=["harper Hoffman", "Jen Pan", "Alex Sanchez", "Amit Ayer"],
        )
        self.assertEqual(slug2, "SOW_Boundaries_Mandy_PartnerAdvisory")
        self.assertLessEqual(len(slug2), 40)

        # Case 3: SOW 1 Observability Catchup
        slug3 = standardize_meeting_slug(
            name="Project_Lens_SOW1_Observability_Catchup",
            attendees=["harper Hoffman", "PartnerAdvisory team"],
        )
        self.assertEqual(slug3, "SOW1_Observability_Mandy_PartnerAdvisory")
        self.assertLessEqual(len(slug3), 40)

        # Case 4: Project Sherpa with prepositions and redundant project name
        slug4 = standardize_meeting_slug(
            name="Discuss Next Steps on Sherpa",
            attendees=["Marcus Balk", "Speaker_Alpha Muschett", "Cameron Mercer", "Drew Oakley"],
            project_name="project_beacon",
        )
        self.assertEqual(slug4, "Next_Steps_Marcus_PartnerAdvisory")
        self.assertLessEqual(len(slug4), 30)

        # Case 5: Preposition stripping without explicit project
        slug5 = standardize_meeting_slug(
            name="Alignment on Phase 2 Architecture with Team",
            attendees=["Marcus Balk", "Cameron Mercer"],
        )
        self.assertEqual(slug5, "Phase2_Architecture_Marcus_Stephane")
        self.assertLessEqual(len(slug5), 40)

        # Case 6: All-PartnerAdvisory team roster with dictionary org key and no double-slugging
        slug6 = standardize_meeting_slug(
            name="Jake_Amit_Miles_PartnerAdvisory",
            attendees={"PartnerAdvisory": [
                "Amit Ayer (Lead Enterprise Architect)",
                "Jake Stark (Participant / LENS Lead)",
                "Miles Erickson (Software Engineer / Technical Contributor)",
            ]},
            project_name="project_lens",
        )
        self.assertEqual(slug6, "Jake_Amit_Miles_PartnerAdvisory")
        self.assertLessEqual(len(slug6), 40)

        # Case 7: Meeting title with redundant PartnerAdvisory suffix prevention
        slug7 = standardize_meeting_slug(
            name="Phase1_Designs_Jen_Amit",
            attendees=["Jen Pan (PartnerAdvisory)", "Amit Ayer (PartnerAdvisory)"],
            project_name="project_lens",
        )
        self.assertEqual(slug7, "Phase1_Designs_Jen_Amit")
        self.assertLessEqual(len(slug7), 40)

    def test_envelope_resolve_with_subfolder(self) -> None:
        """MeetingPathEnvelope routes deliverables and receipts into isolated meeting subfolder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = MeetingPathEnvelope.resolve(
                project_name="project_lens",
                meeting_date="09-15-2026",
                meeting_name="Review_LENS_Designs_Phase_1_Completion",
                meeting_time="0232PM_0300PM_ET",
                subfolder="Phase1_Designs_Jen_Amit",
                repo_root=root,
            )

            expected_out = root / "output" / "project_lens" / "09-15-2026" / "Phase1_Designs_Jen_Amit"
            self.assertEqual(env.output_dir, expected_out)
            self.assertEqual(env.subfolder, "Phase1_Designs_Jen_Amit")
            self.assertEqual(env.deliverable_01.parent, expected_out)
            self.assertEqual(env.qa_report.parent, expected_out)
            self.assertEqual(env.judge_evaluation.parent, expected_out)
            self.assertEqual(env.release_decision.parent, expected_out)
            self.assertEqual(env.intake_package.parent, expected_out)


if __name__ == "__main__":
    unittest.main()

