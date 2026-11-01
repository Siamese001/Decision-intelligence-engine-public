"""tests/test_pipeline_hierarchy_gates.py

Validates RawTranscriptPipeline pre-flight and pre-mutation output hierarchy governance gates:
1. Pre-flight output_dir="auto" resolution
2. Pre-mutation delivery gate halts mutation on malformed directory hierarchy
3. Deliverable filename date formatting complies with MM-DD-YYYY SSOT
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


class TestPipelineHierarchyGates(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preflight_auto_output_dir_resolution(self):
        """Pre-flight dynamically resolves output_dir='auto' via LLM Director & normalize_meeting_date."""
        pipeline = RawTranscriptPipeline()
        metadata = {
            "meeting_name": "Nexus Architecture Review",
            "date": "09/14/2026",
            "time": "09:00 AM - 10:00 AM ET",
        }
        dummy_input = self.test_root / "sample.txt"
        dummy_input.write_text("Speaker: Hello world", encoding="utf-8")

        with patch("dual_pipeline.transcript_pipeline_core.preflight_coordinator.determine_project_directory", return_value="project_nexus"):
            with patch.object(pipeline.u0, "execute", side_effect=RuntimeError("HALT_AFTER_PREFLIGHT")):
                with self.assertRaises(RuntimeError) as ctx:
                    pipeline.run(
                        input_path=str(dummy_input),
                        metadata=metadata,
                        output_dir="auto",
                    )
                self.assertIn("HALT_AFTER_PREFLIGHT", str(ctx.exception))

    def test_preflight_invalid_project_fails_early(self):
        """Phase 1 preflight fails immediately if project is unknown."""
        pipeline = RawTranscriptPipeline()
        metadata = {
            "meeting_name": "Review",
            "date": "10-02-2026",
            "time": "0900",
        }
        dummy_input = self.test_root / "sample.txt"
        dummy_input.write_text("Speaker: Hello world", encoding="utf-8")
        bad_dir = self.test_root / "output" / "invalid_project_name" / "10-02-2026"

        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=str(dummy_input),
                metadata=metadata,
                output_dir=str(bad_dir),
            )
        self.assertIn("Unknown project identifier", str(ctx.exception))

    def test_pre_mutation_gate_blocks_on_hierarchy_failure(self):
        """Pre-mutation gate strictly halts execution before publication if directory validation fails."""
        pipeline = RawTranscriptPipeline()
        metadata = {
            "meeting_name": "Nexus Review",
            "date": "10-02-2026",
            "time": "0900",
            "attendees": [{"name": "Jen", "role": "Lead Architect", "organization": "Apex Global Enterprises"}],
        }
        dummy_input = self.test_root / "sample.txt"
        dummy_input.write_text("Jen: Hello world", encoding="utf-8")

        output_dir = self.test_root / "output" / "project_nexus" / "10-02-2026"
        output_dir.mkdir(parents=True)
        t_path = output_dir / "01_transcript.md"
        g_path = output_dir / "03_graph.json"

        # Mock validate_meeting_directory to return violations
        with patch("dual_pipeline.transcript_pipeline_core.release_runner.validate_meeting_directory", return_value=["Missing deliverable 01"]):
            with patch.object(pipeline, "_uwg_write", return_value={
                "01_transcript": str(t_path),
                "02_recap": str(output_dir / "02_recap.md"),
                "03_graph": str(g_path),
            }):
                with patch("dual_pipeline.transcript_pipeline_core.release_runner.prepare_package", return_value={"package": "stub"}):
                    with patch.object(pipeline.x_gates, "execute", return_value=True):
                        with patch("dual_pipeline.transcript_pipeline_core.qa_runner.evaluate_judge_and_release", return_value=(
                            {"passed": True},
                            {},
                            {"authoritative_write_authorized": True, "status": "PASS", "output_1_pass": True, "output_2_pass": True, "output_3_pass": True}
                        )):
                            with patch.object(pipeline.l2_forensic, "execute", return_value="Transcript"):
                                with patch.object(pipeline.l2_recap, "execute", return_value="Recap"):
                                    with patch.object(pipeline.l2_graph, "execute", return_value=json.dumps({"schema_version": "5.7"})):
                                        with self.assertRaises(RuntimeError) as ctx:
                                            pipeline.run(
                                                input_path=str(dummy_input),
                                                metadata=metadata,
                                                output_dir=str(output_dir),
                                                mutate_graph=True,
                                            )
                                        self.assertIn("Output directory hierarchy compliance failed before publication", str(ctx.exception))

    def test_uwg_write_preserves_hyphenated_date_stem(self):
        """_uwg_write uses normalize_meeting_date to format MM-DD-YYYY in file stems."""
        pipeline = RawTranscriptPipeline()
        output_dir = self.test_root / "project_nexus" / "10-02-2026"
        output_dir.mkdir(parents=True)

        artifacts = {
            "01_hitl_resolved_transcript.md": "# Transcript\n",
            "02_executive_recap.md": "# Recap\n",
            "03_canonical_signal_projection.json": json.dumps({"schema_version": "1.0.0"}),
        }
        metadata = {
            "meeting_name": "Arch Review",
            "date": "09/14/2026",  # Slash date input
            "time": "10:00 AM",
        }

        written = pipeline._uwg_write(artifacts, metadata, str(output_dir))
        t_path = Path(written["01_transcript"])
        # Filename must contain MM-DD-YYYY, not stripped 09142026
        self.assertIn("10-02-2026", t_path.name)
        self.assertTrue(t_path.exists())


if __name__ == "__main__":
    unittest.main()
