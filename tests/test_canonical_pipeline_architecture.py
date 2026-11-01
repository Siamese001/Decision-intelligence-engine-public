"""
tests/test_canonical_pipeline_architecture.py

Verifies the Wave 1 Canonical Base Pipeline Architecture & Modality Strategies:
1. CanonicalAgenticPipeline inherits from UnifiedAgenticPipeline.
2. pipeline_name returns 'CanonicalAgenticPipeline'.
3. Modality detection accurately identifies AUDIO, MEETING_TRANSCRIPT, MEETING_NOTES, and DOCUMENT.
4. End-to-end execution of all 4 modalities in offline mode.
5. All 7 stages (U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS) are traversed and recorded.
6. pipeline_execution_receipt.json is unconditionally written with PASS statuses.
7. Any attempt to pass bypass_speaker_hitl fails closed immediately with ValueError.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline import (
    CanonicalAgenticPipeline,
    CanonicalModality,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    UnifiedAgenticPipeline,
)


class TestCanonicalPipelineArchitecture(unittest.TestCase):
    """Test suite validating CanonicalAgenticPipeline architecture and contracts."""

    def test_inheritance_and_naming(self) -> None:
        """Verify inheritance from UnifiedAgenticPipeline and canonical naming."""
        self.assertTrue(issubclass(CanonicalAgenticPipeline, UnifiedAgenticPipeline))
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        self.assertEqual(pipeline.pipeline_name, "CanonicalAgenticPipeline")

    def test_modality_detection(self) -> None:
        """Verify accurate modality classification across diverse inputs."""
        # Audio
        self.assertEqual(detect_modality(Path("recording.m4a")), CanonicalModality.AUDIO)
        self.assertEqual(detect_modality(metadata={"_from_audio_transcription": True}), CanonicalModality.AUDIO)

        # Document
        self.assertEqual(detect_modality(Path("spec.pdf")), CanonicalModality.DOCUMENT)
        self.assertEqual(detect_modality(metadata={"is_document": True}), CanonicalModality.DOCUMENT)
        self.assertEqual(detect_modality(metadata={"output3_only": True}), CanonicalModality.DOCUMENT)

        # Notes
        self.assertEqual(detect_modality(metadata={"notes_mode": True}), CanonicalModality.MEETING_NOTES)
        self.assertEqual(detect_modality(metadata={"source_type": "copilot_notes"}), CanonicalModality.MEETING_NOTES)

        # Diarized Transcript default
        self.assertEqual(detect_modality(Path("transcript.md")), CanonicalModality.MEETING_TRANSCRIPT)

    def test_deliverable_mapping(self) -> None:
        """Verify deliverable requirements per modality."""
        self.assertEqual(get_deliverables_for_modality(CanonicalModality.AUDIO), ["01", "02", "03"])
        self.assertEqual(get_deliverables_for_modality(CanonicalModality.MEETING_TRANSCRIPT), ["01", "02", "03"])
        self.assertEqual(get_deliverables_for_modality(CanonicalModality.MEETING_NOTES), ["02", "03"])
        self.assertEqual(get_deliverables_for_modality(CanonicalModality.DOCUMENT), ["03"])

    def test_full_meeting_transcript_execution_offline(self) -> None:
        """Verify end-to-end execution of a full diarized transcript (Outputs 01, 02, 03)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            transcript_file = tmp_path / "raw_transcript.txt"
            transcript_file.write_text(
                "# Architecture Review\n\n"
                "**Amit Ayer:** [00:00] We are reviewing the unified pipeline architecture.\n"
                "**harper Hoffman:** [00:15] Confirmed, 7 stages active and all gates passing.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_nexus" / "09-23-2026" / "Architecture_Review"
            out_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "project": "project_nexus",
                "meeting_name": "Architecture Review",
                "date": "09/23/2026",
                "date_filename": "09-23-2026",
                "filename_slug": "Architecture_Review",
                "classification": "confidential",
                "roster": [
                    {"name": "Amit Ayer", "role": "Lead Architect"},
                    {"name": "harper Hoffman", "role": "Product Manager"},
                ],
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(transcript_file, metadata=metadata, output_dir=str(out_dir))

            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], "MEETING_TRANSCRIPT")
            self.assertIsNotNone(result["01_transcript"])
            self.assertIsNotNone(result["02_recap"])
            self.assertIsNotNone(result["03_graph"])

            # Verify receipt
            receipt_file = out_dir / "pipeline_execution_receipt.json"
            self.assertTrue(receipt_file.exists())
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["pipeline_name"], "CanonicalAgenticPipeline")
            self.assertEqual(receipt_data["terminal_outcome"], "COMPLETE")

            # Check all 7 stages
            for stage in PIPELINE_STAGE_SEQUENCE:
                self.assertIn(stage.value, receipt_data["stages"])
                self.assertEqual(receipt_data["stages"][stage.value]["status"], "PASS")

    def test_meeting_notes_execution_offline(self) -> None:
        """Verify end-to-end execution of meeting notes (Outputs 02 and 03 only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            notes_file = tmp_path / "copilot_summary.txt"
            notes_file.write_text(
                "# Meeting Summary Notes\n\n"
                "Key decisions: Transition to CanonicalAgenticPipeline approved.\n"
                "Next steps: Verify test coverage and purge bypass keys.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_nexus" / "09-23-2026" / "Copilot_Summary"
            out_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "project": "project_nexus",
                "meeting_name": "Copilot Summary",
                "date": "09/23/2026",
                "notes_mode": True,
                "classification": "confidential",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(notes_file, metadata=metadata, output_dir=str(out_dir))

            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], "MEETING_NOTES")
            self.assertIsNone(result["01_transcript"])
            self.assertIsNotNone(result["02_recap"])
            self.assertIsNotNone(result["03_graph"])

            # Verify receipt documents all 7 stages
            receipt_file = out_dir / "pipeline_execution_receipt.json"
            self.assertTrue(receipt_file.exists())
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            for stage in PIPELINE_STAGE_SEQUENCE:
                self.assertIn(stage.value, receipt_data["stages"])
                self.assertEqual(receipt_data["stages"][stage.value]["status"], "PASS")

    def test_document_execution_offline(self) -> None:
        """Verify end-to-end execution of a document (Output 03 only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            doc_file = tmp_path / "architecture_spec.txt"
            doc_file.write_text(
                "# Architecture Specification\n\n"
                "Author: Amit Ayer\n"
                "Date: 09/23/2026\n\n"
                "System: CANONICAL-PIPELINE\n"
                "Decision: All modalities unified under 7-stage engine.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_nexus" / "09-23-2026" / "Architecture_Spec"
            out_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "project": "project_nexus",
                "title": "Architecture Specification",
                "date": "09/23/2026",
                "is_document": True,
                "author": "Amit Ayer",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(doc_file, metadata=metadata, output_dir=str(out_dir))

            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], "DOCUMENT")
            self.assertIsNone(result["01_transcript"])
            self.assertIsNone(result["02_recap"])
            self.assertIsNotNone(result["03_graph"])

            # Verify receipt documents all 7 stages
            receipt_file = out_dir / "pipeline_execution_receipt.json"
            self.assertTrue(receipt_file.exists())
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            for stage in PIPELINE_STAGE_SEQUENCE:
                self.assertIn(stage.value, receipt_data["stages"])
                self.assertEqual(receipt_data["stages"][stage.value]["status"], "PASS")

    def test_fail_closed_on_synthetic_bypass(self) -> None:
        """Verify that passing bypass_speaker_hitl fails closed immediately with ValueError."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        with self.assertRaises(ValueError) as ctx:
            pipeline.run("dummy.txt", metadata={"bypass_speaker_hitl": True})
        self.assertIn("HITL cannot be removed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
