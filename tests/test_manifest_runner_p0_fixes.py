"""
tests/test_manifest_runner_p0_fixes.py

Wave 1 Acceptance Tests:
Verifies P0 correctness and safety fixes in manifest runner:
1. Participant and attendee provenance preservation (eliminating hardcoded "PartnerAdvisory" overwrite).
2. Governed in-process intake-publish execution (eliminating raw subprocess CLI shell-outs).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.manifest_runner.executor import _execute_graph_publish, _seal_intake_package
from tests.characterization.test_fixtures import (
    SAMPLE_CANONICAL_PROJECTION_DATA,
    SAMPLE_TEAMS_RAW_TRANSCRIPT,
)


class TestManifestRunnerP0Fixes(unittest.TestCase):
    """Verifies that manifest runner preserves provenance and executes in-process."""

    def test_attendee_and_speaker_provenance_preservation(self) -> None:
        """Verifies authentic attendee names from manifest are preserved in sealed package."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            out_dir = base / "output" / "project_nexus" / "08-28-2026"
            out_dir.mkdir(parents=True, exist_ok=True)
            intake_dir = base / "intake"
            intake_dir.mkdir(parents=True, exist_ok=True)

            manifest = IngestionMeetingManifest(
                project_name="project_nexus",
                meeting_title="Architecture Integration Review",
                meeting_date="08/28/2026",
                start_time_et="10:00 AM",
                end_time_et="10:30 AM",
                raw_text=SAMPLE_TEAMS_RAW_TRANSCRIPT,
                meeting_objective="Test Objective",
                attendees=[
                    {"name": "Dan", "company": "PartnerAdvisory", "role": "Architect"},
                    {"name": "Sarah", "company": "Client", "role": "Lead"},
                ],
                f3_data=SAMPLE_CANONICAL_PROJECTION_DATA,
                output_root=base / "output",
                intake_dir=intake_dir,
            )
            manifest.resolved_output_dir.mkdir(parents=True, exist_ok=True)
            manifest.f1_path.write_text(SAMPLE_TEAMS_RAW_TRANSCRIPT, encoding="utf-8")
            manifest.f3_path.write_text(json.dumps(SAMPLE_CANONICAL_PROJECTION_DATA), encoding="utf-8")

            _seal_intake_package(manifest)

            self.assertTrue(manifest.pkg_path.is_file())
            pkg = json.loads(manifest.pkg_path.read_text(encoding="utf-8"))

            # Verify authentic attendees preserved
            attendees = pkg["meeting"]["attendees"]
            self.assertIn("Dan", attendees)
            self.assertIn("Sarah", attendees)
            # Ensure it was not stripped or forcibly overwritten with only ["PartnerAdvisory"]
            self.assertNotEqual(attendees, ["PartnerAdvisory"])

    @patch("dual_pipeline.cli.handlers._intake_publish")
    @patch("subprocess.run")
    def test_in_process_intake_publish_execution(self, mock_subprocess: MagicMock, mock_publish: MagicMock) -> None:
        """Verifies that _execute_graph_publish executes in-process and never calls subprocess.run."""
        mock_publish.return_value = {"status": "COMPLETE"}

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            out_dir = base / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            intake_dir = base / "intake"
            intake_dir.mkdir(parents=True, exist_ok=True)

            manifest = IngestionMeetingManifest(
                project_name="project_nexus",
                meeting_title="Architecture Integration Review",
                meeting_date="09/12/2026",
                start_time_et="10:00 AM",
                end_time_et="10:30 AM",
                raw_text=SAMPLE_TEAMS_RAW_TRANSCRIPT,
                meeting_objective="Test Objective",
                attendees=[{"name": "Dan", "company": "PartnerAdvisory"}],
                f3_data=SAMPLE_CANONICAL_PROJECTION_DATA,
                output_root=out_dir,
                intake_dir=intake_dir,
            )
            manifest.pkg_path.write_text(json.dumps({"test": "package"}), encoding="utf-8")

            # Mock active pair files
            mock_pair = {
                "graph_path": str(base / "graph.db"),
                "sidecar_path": str(base / "sidecar.db"),
                "graph_sha256": "mock-hash",
                "generation_id": "gen-01",
            }
            with patch("dual_pipeline.manifest_runner.executor.query_graph_counts", return_value={}), \
                 patch("dual_pipeline.manifest_runner.executor.query_sidecar_counts", return_value={}), \
                 patch("dual_pipeline.manifest_runner.executor.query_new_assertions", return_value=[]), \
                 patch("dual_pipeline.manifest_runner.executor.print_mutation_breakdown"), \
                 patch("pathlib.Path.read_text", return_value=json.dumps(mock_pair)):
                res = _execute_graph_publish(manifest)

            # subprocess.run MUST NOT be called
            mock_subprocess.assert_not_called()
            # in-process _intake_publish MUST be called
            mock_publish.assert_called_once()
            self.assertEqual(res["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
