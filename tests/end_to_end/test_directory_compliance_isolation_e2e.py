"""
tests/end_to_end/test_directory_compliance_isolation_e2e.py

End-to-End test suite exercising scoped directory compliance, staging promotion,
and release packaging lifecycle under dirty sibling folder conditions.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.lifecycle import (
    build_and_validate_intake_package,
    check_directory_compliance,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    prepare_and_verify_release_package,
    promote_staging_to_release,
)
from tools.validate_output_hierarchy import validate_meeting_directory


class TestDirectoryComplianceIsolationE2E(unittest.TestCase):
    """E2E suite verifying full release packaging & compliance under dirty conditions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "output"
        self.project_dir = self.output_dir / "gemini_enablement"
        self.date_dir = self.project_dir / "09-30-2026"
        self.date_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _setup_compliant_staging(self, staging_dir: Path, stem: str, meeting_date: str) -> dict[str, str]:
        """Creates fully compliant deliverables and metadata in staging directory."""
        staging_dir.mkdir(parents=True, exist_ok=True)
        t_path = staging_dir / f"01_{stem}_hitl_resolved_transcript.md"
        r_path = staging_dir / f"02_{stem}_executive_recap.md"
        g_path = staging_dir / f"03_{stem}_canonical_signal_projection.json"

        t_path.write_text(
            f"# Transcript for {stem}\n\n"
            "[00:00:00] Amit Ayer: Hello everyone, welcome to the working session on data pipelines and architecture. "
            "Today we will discuss our deliverables, timelines, milestones, and how the team is progressing across our quarterly initiatives. "
            "Please ensure all action items are captured in our tracker and that each stakeholder reviews the governance notes.\n\n"
            "[00:01:00] Rose Teame: Sounds great, let's get started on the agenda.",
            encoding="utf-8",
        )
        r_path.write_text(f"# Recap\n\nDate: {meeting_date}\n\n## Objective\nAlign.", encoding="utf-8")
        g_path.write_text(json.dumps({"schema_version": "5.7", "status": "PASS"}), encoding="utf-8")

        qa_path = staging_dir / "qa_gates_report.json"
        judge_path = staging_dir / "llm_judge_evaluation.json"
        decision_path = staging_dir / "final_release_decision.json"

        qa_path.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        judge_path.write_text(json.dumps({"status": "PASS", "overall_verdict": "PASS"}), encoding="utf-8")
        decision_path.write_text(json.dumps({"status": "PASS", "authoritative_write_authorized": True}), encoding="utf-8")

        meta = {
            "project": "gemini_enablement",
            "date": meeting_date,
            "meeting_name": "Working_Files_Data_Rose_Amanda",
            "classification": "confidential",
            "author": "Amit Ayer",
        }
        (staging_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        return {
            "01_transcript": str(t_path),
            "02_recap": str(r_path),
            "03_graph": str(g_path),
        }

    def test_e2e_lifecycle_release_with_dirty_sibling_folders(self) -> None:
        """Full intake package generation, staging promotion, and publication verification with dirty siblings."""
        # 1. Dirty sibling: an abandoned meeting directory missing 01/02 and receipts
        abandoned_sibling = self.date_dir / "Gemini_Working_Files_Data"
        abandoned_sibling.mkdir(parents=True, exist_ok=True)
        (abandoned_sibling / "scratch.txt").write_text("incomplete", encoding="utf-8")

        # 2. Dirty sibling: a stale staging folder from a killed previous process
        stale_staging = self.date_dir / ".staging_abandoned_session_998877"
        stale_staging.mkdir(parents=True, exist_ok=True)
        (stale_staging / "chunk_00000.wav").write_bytes(b"RIFFdummyWAVEfmt ")

        # 3. Prepare target session in a new isolated staging dir
        active_staging = self.date_dir / ".staging_working_files_active_112233"
        stem = "Working_Files_Data_Rose_Amanda_09-30-2026_1000AM_1030AM_ET"
        artifacts = self._setup_compliant_staging(active_staging, stem, "09-30-2026")

        meta = {
            "project": "gemini_enablement",
            "date": "09-30-2026",
            "meeting_name": "Working_Files_Data_Rose_Amanda",
            "classification": "confidential",
            "author": "Amit Ayer",
        }

        # 4. Build intake package in staging
        pkg_path = build_and_validate_intake_package(
            out_p=active_staging,
            artifacts_map=artifacts,
            meta=meta,
            source_text="Amit Ayer: Hello.",
            src_file_path=Path(artifacts["01_transcript"]),
        )
        self.assertTrue(pkg_path.is_file())

        # 5. Atomically promote to target directory: output/gemini_enablement/09-30-2026/Working_Files_Data_Rose_Amanda
        target_out = self.date_dir / "Working_Files_Data_Rose_Amanda"
        promote_staging_to_release(active_staging, target_out, purge_staging=True)

        self.assertFalse(active_staging.exists(), "Active staging directory must be purged upon promotion")
        self.assertTrue((target_out / pkg_path.name).is_file(), "Intake package must be promoted")

        # 6. Verify directory compliance succeeds on target_out despite dirty abandoned_sibling
        check_directory_compliance(target_out, proj="gemini_enablement")

        # 7. Verify release package validation in release_runner passes cleanly
        written = {
            "01_transcript": str(target_out / f"01_{stem}_hitl_resolved_transcript.md"),
            "02_recap": str(target_out / f"02_{stem}_executive_recap.md"),
            "03_graph": str(target_out / f"03_{stem}_canonical_signal_projection.json"),
        }
        verified_pkg = prepare_and_verify_release_package(
            written=written,
            metadata=meta,
            artifacts={},
            is_notes_mode=False,
        )
        self.assertTrue(verified_pkg.is_file())

        # 8. Verify stale staging directories were automatically purged during release verification
        self.assertFalse(stale_staging.exists(), "Stale staging directories must be purged by release runner")

        # 9. Target meeting container remains 100% compliant
        violations = validate_meeting_directory(target_out, project_name="gemini_enablement")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
