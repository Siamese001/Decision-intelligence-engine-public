"""
tests/end_to_end/test_active_ingestion_entrypoints_e2e.py

Wave 4 E2E Test Suite: Active Ingestion Entrypoint Matrix.
Verifies that all production ingestion entrypoints (meeting, document, notes,
and canonical pipeline) default to active graph intent without mutation flags,
reach approved publication or visible pending/blocked states, and never produce
silent artifact-only success.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
from dual_pipeline.notes_pipeline import NotesPipeline
from dual_pipeline.pipeline_runner import TranscriptIngestionPipelineRunner
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


class ActiveIngestionEntrypointsE2ETests(unittest.TestCase):
    """E2E suite verifying active graph intent defaults across all ingestion entrypoints."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_canonical_pipeline_defaults_to_active_intent(self) -> None:
        """Verify CanonicalAgenticPipeline.run defaults to mutate_graph=True."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        import inspect
        sig = inspect.signature(pipeline.run)
        self.assertIn("mutate_graph", sig.parameters)
        self.assertTrue(sig.parameters["mutate_graph"].default)
        self.assertTrue(sig.parameters["auto_mutate_on_pass"].default)

        from dual_pipeline.canonical_pipeline.publish_helper import prepare_run_metadata
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
        meta, eff_mut, eff_auto = prepare_run_metadata(
            forced_modality=CanonicalModality.MEETING_TRANSCRIPT,
            provider_type="offline",
            metadata={},
            directive_id="dir-test",
            presentation_path=None,
            signal_envelope=None,
            auto_mutate_on_pass=True,
            mutate_graph=True,
            kwargs={},
        )
        self.assertTrue(eff_mut)
        self.assertTrue(eff_auto)

    def test_raw_transcript_pipeline_defaults_to_active_intent(self) -> None:
        """Verify RawTranscriptPipeline defaults mutate_graph=True."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        import inspect
        sig = inspect.signature(pipeline.run)
        self.assertIn("mutate_graph", sig.parameters)
        self.assertTrue(sig.parameters["mutate_graph"].default)
        self.assertTrue(sig.parameters["auto_mutate_on_pass"].default)

    def test_notes_pipeline_defaults_to_active_intent(self) -> None:
        """Verify NotesPipeline defaults mutate_graph=True."""
        pipeline = NotesPipeline(provider_type="offline")
        import inspect
        sig = inspect.signature(pipeline.run)
        self.assertIn("mutate_graph", sig.parameters)
        self.assertTrue(sig.parameters["mutate_graph"].default)

    def test_document_pipeline_defaults_to_active_intent(self) -> None:
        """Verify DocumentPipeline defaults mutate_graph=True."""
        pipeline = DocumentPipeline(provider_type="offline")
        import inspect
        sig = inspect.signature(pipeline.run)
        self.assertIn("mutate_graph", sig.parameters)
        self.assertTrue(sig.parameters["mutate_graph"].default)

    def test_cli_ingest_canonical_defaults_to_active_and_awaits_approval(self) -> None:
        """Verify CLI ingest_canonical defaults to active intent and routes missing approval to AWAITING_APPROVAL."""
        source_file = self.tmp_path / "sample.md"
        source_file.write_text("# Meeting\nAlice: Testing active intent.", encoding="utf-8")

        args = argparse.Namespace(
            input=source_file,
            project="project_nexus",
            meeting="CLI Active Ingestion",
            date="10/06/2026",
            provider="offline",
            metadata=None,
            output_dir=str(self.tmp_path),
            directive_id="dir-active-01",
            reviewer=None,
            rationale=None,
            dry_run=False,
            dry_run_graph=False,
            # Note: mutate_graph NOT set on args; defaults to True
        )

        with patch("dual_pipeline.hitl_governance_core.active_ingestion_approval.check_active_ingestion_clearance") as mock_clear, \
             patch("dual_pipeline.hitl_governance_core.active_ingestion_approval.dispatch_and_persist_approval_request") as mock_disp:

            mock_clear.return_value = (False, "NO_PRIOR_APPROVAL", None)
            mock_disp.return_value = {
                "request_id": "req-001",
                "response_status": "AWAITING_INPUT",
            }

            result = ingest_canonical(args)

            self.assertEqual(result.get("status"), "AWAITING_APPROVAL")
            self.assertEqual(result.get("directive_id"), "dir-active-01")
            self.assertFalse(result.get("graph_mutation_committed"))
            self.assertTrue(mock_disp.called)

    def test_pipeline_runner_returns_package_sealed_not_complete(self) -> None:
        """Verify PipelineRunner reports PACKAGE_SEALED for artifact-only preparation."""
        runner = TranscriptIngestionPipelineRunner()
        runner.parse_transcript = MagicMock(return_value=("raw text", []))
        runner.build_output_01 = MagicMock(return_value="f1 text")
        runner.build_output_02 = MagicMock(return_value="f2 text")
        runner.build_output_03 = MagicMock(return_value={})

        manifest = MagicMock()
        manifest.resolved_base_name = "test_meeting"
        manifest.resolved_output_dir = self.tmp_path / "out"
        manifest.resolved_intake_dir = self.tmp_path / "intake"
        manifest.pkg_path = self.tmp_path / "pkg.json"
        manifest.source_transcript_path = self.tmp_path / "source.txt"
        manifest.f1_path = self.tmp_path / "f1.txt"
        manifest.f2_path = self.tmp_path / "f2.txt"
        manifest.f3_path = self.tmp_path / "f3.json"
        manifest.qa_report_path = self.tmp_path / "qa_report.json"
        manifest.classification = "internal"
        manifest.audiences = ["team"]
        manifest.meeting_title = "Test Meeting"
        manifest.human_date = "10/06/2026"
        manifest.time_str = "10:00"
        manifest.meeting_archetype = "general"
        manifest.attendees = []
        manifest.roster = []
        manifest.expected_domain_terms = []
        manifest.to_metadata_dict.return_value = {}

        with patch("dual_pipeline.pipeline_runner.evaluate_all_qa_gates", return_value={"passed": True}), \
             patch("dual_pipeline.pipeline_runner.evaluate_judge_and_release", return_value=({}, {"final_release_verdict": "PASS", "scores": {"output_3_canonical_signal_projection": 95}})), \
             patch("dual_pipeline.pipeline_runner.prepare_package"):
            result = runner.run(manifest)
            self.assertEqual(result.get("status"), "PACKAGE_SEALED")


if __name__ == "__main__":
    unittest.main()
