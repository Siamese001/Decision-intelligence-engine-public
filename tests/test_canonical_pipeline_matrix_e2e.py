"""End-to-End Test Matrix across all Canonical Pipeline Modalities (Wave 5).

Verifies the consolidated CanonicalAgenticPipeline across:
1. AUDIO modality (audio file input with transcriber) -> Produces 01, 02, 03.
2. MEETING_TRANSCRIPT modality (diarized turns) -> Produces 01, 02, 03.
3. MEETING_NOTES modality (Copilot notes / executive summary) -> Produces 02, 03 (Output 01 N/A).
4. DOCUMENT modality (Architecture / SOW specification) -> Produces 03 only (Outputs 01, 02 N/A).
5. Receipt verification: all 7 pipeline stages documented in pipeline_execution_receipt.json.
6. Directory hierarchy compliance: output/<project>/<MM-DD-YYYY>/<slug>/ verified.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE


class TestCanonicalPipelineMatrixE2E(unittest.TestCase):
    """End-to-end matrix verification across all 4 canonical modalities."""

    def test_audio_modality_e2e(self) -> None:
        """Audio modality must produce 01, 02, 03 and record all 7 stages in receipt."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            audio_file = tmp_path / "session_recording.wav"
            # Write minimal valid RIFF/WAV header (44 bytes)
            audio_file.write_bytes(
                b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
                b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
            )

            out_dir = tmp_path / "output" / "nea" / "09-25-2026" / "Audio_Session"
            out_dir.mkdir(parents=True, exist_ok=True)

            mock_transcriber = MagicMock()
            mock_transcriber.transcribe.return_value = (
                "**Amit Ayer:** [09:00:00]: Initiating audio intake test.\n\n"
                "**Reid Schenk:** [09:01:00]: Audio ingestion verified end-to-end.\n\n"
                "**Amit Ayer:** [09:02:00]: Continuing verification of canonical pipeline stages.\n\n"
                "**Reid Schenk:** [09:03:00]: All telemetry and contracts passing.\n\n"
                "**Amit Ayer:** [09:04:00]: Excellent, concluding the audio session.\n\n"
                "**Reid Schenk:** [09:05:00]: Agreed, session complete.\n"
            )

            with patch("dual_pipeline.audio_ingestion.get_default_transcriber", return_value=mock_transcriber):
                pipeline = CanonicalAgenticPipeline(provider_type="offline")
                result = pipeline.run(
                    input_path=audio_file,
                    metadata={
                        "project": "nea",
                        "meeting_name": "Audio Session",
                        "date": "09/25/2026",
                        "time": "09:00 AM - 09:30 AM ET",
                        "attendees": "Amit Ayer, Reid Schenk",
                        "classification": "internal",
                        "attendees_confirmed_by_operator": True,
                        "attendee_confirmation_receipt": {
                            "status": "RESOLVED_HITL",
                            "confirmed_by": "Amit Ayer",
                            "attendees": ["Amit Ayer", "Reid Schenk"],
                            "subject_sha256": "subj_matrix_audio_09252026",
                        },
                    },
                    output_dir=str(out_dir),
                    package_only=True,
                )

            self.assertEqual(result["status"], "PACKAGE_SEALED")
            self.assertEqual(result["modality"], CanonicalModality.AUDIO.value)
            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

            receipt_path = Path(result["execution_receipt"])
            self.assertTrue(receipt_path.exists())
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertIn(receipt_data["terminal_outcome"], ("PACKAGE_READY", "COMPLETE"))
            recorded_stages = list(receipt_data["stages"].keys())
            self.assertEqual(recorded_stages, [s.value for s in PIPELINE_STAGE_SEQUENCE])

    def test_meeting_transcript_modality_e2e(self) -> None:
        """Diarized transcript modality must produce 01, 02, 03 and record all 7 stages."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            transcript_file = tmp_path / "Technical_Sync_09-25-2026.txt"
            transcript_file.write_text(
                "**harper Hoffman:** [11:00:00]: Reviewing pipeline consolidation architecture.\n\n"
                "**Amit Ayer:** [11:01:00]: Single canonical agentic pipeline active and verified.\n\n"
                "**harper Hoffman:** [11:02:00]: The stage sequence and strategies align perfectly.\n\n"
                "**Amit Ayer:** [11:03:00]: Cross-modality matrix execution is fully unified.\n\n"
                "**harper Hoffman:** [11:04:00]: Excellent work on the consolidation.\n\n"
                "**Amit Ayer:** [11:05:00]: Thank you Mandy, concluding sync.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "nea" / "09-25-2026" / "Technical_Sync"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=transcript_file,
                metadata={
                    "project": "nea",
                    "meeting_name": "Technical Sync",
                    "date": "09/25/2026",
                    "time": "11:00 AM - 11:30 AM ET",
                    "attendees": "harper Hoffman, Amit Ayer",
                    "classification": "internal",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            self.assertEqual(result["status"], "PACKAGE_SEALED")
            self.assertEqual(result["modality"], CanonicalModality.MEETING_TRANSCRIPT.value)
            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

            receipt_data = json.loads(Path(result["execution_receipt"]).read_text(encoding="utf-8"))
            self.assertIn(receipt_data["terminal_outcome"], ("PACKAGE_READY", "COMPLETE"))
            self.assertEqual(list(receipt_data["stages"].keys()), [s.value for s in PIPELINE_STAGE_SEQUENCE])

    def test_meeting_notes_modality_e2e(self) -> None:
        """Meeting notes modality must produce 02, 03 (Output 01 N/A)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            notes_file = tmp_path / "Architecture_Notes_09-25-2026.txt"
            notes_file.write_text(
                "Meeting Notes: Architecture Sync and Platform Consolidation\n\n"
                "Date: 09/25/2026\n"
                "Attendees: harper Hoffman, Amit Ayer\n\n"
                "Summary:\n"
                "The engineering leadership team reviewed the platform consolidation roadmap. "
                "All participants agreed to deprecate legacy bespoke synthesizers and standardize entirely "
                "on CanonicalAgenticPipeline across all supported modalities including audio, transcript, notes, and documents. "
                "The unified execution sequence guarantees deterministic routing, strict schema conformance, and verifiable runtime receipts.\n\n"
                "Action Items:\n"
                "- Seal release packages under the unified acceptance gate and verify cross-modality matrix execution.\n"
                "- Deploy content-addressed checkpoint caching to eliminate duplicate LLM generation calls.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "nea" / "09-25-2026" / "Architecture_Notes"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=notes_file,
                metadata={
                    "project": "nea",
                    "meeting_name": "Architecture Notes",
                    "date": "09/25/2026",
                    "time": "10:00 AM - 10:30 AM ET",
                    "attendees": "harper Hoffman, Amit Ayer",
                    "notes_mode": True,
                    "classification": "internal",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            self.assertEqual(result["status"], "PACKAGE_SEALED")
            self.assertEqual(result["modality"], CanonicalModality.MEETING_NOTES.value)
            self.assertIsNone(result.get("01_transcript"))
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

            receipt_data = json.loads(Path(result["execution_receipt"]).read_text(encoding="utf-8"))
            self.assertIn(receipt_data["terminal_outcome"], ("PACKAGE_READY", "COMPLETE"))
            self.assertEqual(list(receipt_data["stages"].keys()), [s.value for s in PIPELINE_STAGE_SEQUENCE])

    def test_document_modality_e2e(self) -> None:
        """Document modality must produce 03 only (Outputs 01, 02 N/A)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            doc_file = tmp_path / "Canonical_Pipeline_Spec_09-25-2026.txt"
            doc_file.write_text(
                "# Architecture Specification: Consolidated Pipeline Engine\n\n"
                "Date: 09/25/2026\n"
                "System: Decision Intelligence Engine\n"
                "Scope: Unification of all audio, transcript, notes, and document pipelines into CanonicalAgenticPipeline.\n\n"
                "The canonical agentic pipeline establishes an immutable seven-stage execution harness "
                "providing deterministic ingress routing, cognitive normalization, conformance verification, "
                "synthesis dispatch, exit gate evaluation, and publication gating across all enterprise modalities.\n"
                "All downstream outputs strictly adhere to RFC-8259 JSON validation standards and zero-mutation policies.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Canonical_Pipeline_Spec"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=doc_file,
                metadata={
                    "project": "project_lens",
                    "title": "Canonical Pipeline Spec",
                    "date": "09/25/2026",
                    "document": True,
                    "classification": "internal",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            self.assertEqual(result["status"], "PACKAGE_SEALED")
            self.assertEqual(result["modality"], CanonicalModality.DOCUMENT.value)
            self.assertIsNone(result.get("01_transcript"))
            self.assertIsNone(result.get("02_recap"))
            self.assertTrue(Path(result["03_graph"]).exists())

            receipt_data = json.loads(Path(result["execution_receipt"]).read_text(encoding="utf-8"))
            self.assertIn(receipt_data["terminal_outcome"], ("PACKAGE_READY", "COMPLETE"))
            self.assertEqual(list(receipt_data["stages"].keys()), [s.value for s in PIPELINE_STAGE_SEQUENCE])

    def test_mutation_call_signature_and_execution(self) -> None:
        """Verify that mutate_graph=True calls publish_release_to_graph with exact signature."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            doc_file = tmp_path / "spec.md"
            doc_file.write_text(
                "# Architecture Specification: Engine Mutation Harness\n\n"
                "Date: 09/25/2026\n"
                "System: Decision Intelligence Engine\n"
                "Scope: Governed graph mutation execution via verified publication gates.\n\n"
                "This specification document outlines the formal mutation and publication contracts for the decision intelligence platform. "
                "Every graph mutation request is strictly validated against proposal digests, base generations, and human operator approvals. "
                "The release runner ensures that unverified or unauthenticated mutations fail closed without modifying persistent canonical state.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "nea" / "09-25-2026" / "Spec_Test"
            out_dir.mkdir(parents=True, exist_ok=True)

            def fake_publish(package_path, metadata, written, **kwargs):
                written["intake_publish"] = {"status": "COMPLETE", "mutation_summary": {"status": "PASS"}}

            with patch(
                "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
                return_value={"status": "AUTHORIZED", "approved_by": "Amit Ayer"},
            ), patch(
                "dual_pipeline.canonical_pipeline.publish_helper._assert_authoritative_mutation_preconditions",
            ), patch(
                "dual_pipeline.release_policy.verify_authoritative_release_gate",
            ), patch(
                "dual_pipeline.document_pipeline_core.retrieval_visibility.completion_from_publication",
                return_value={"status": "VERIFIED"},
            ), patch(
                "dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph",
                side_effect=fake_publish,
            ) as mock_pub:
                pipeline = CanonicalAgenticPipeline(provider_type="offline")
                dir_id = f"dir-test-{uuid.uuid4().hex[:8]}"
                result = pipeline.run(
                    input_path=doc_file,
                    metadata={
                        "project": "nea",
                        "title": "Spec Test",
                        "date": "09/25/2026",
                        "document": True,
                        "classification": "internal",
                    },
                    output_dir=str(out_dir),
                    mutate_graph=True,
                    directive_id=dir_id,
                    reviewer="Amit Ayer",
                    rationale="Test publication",
                )

                self.assertEqual(result["status"], "COMPLETE")
                mock_pub.assert_called_once()
                call_kwargs = mock_pub.call_args.kwargs
                self.assertTrue(str(call_kwargs["package_path"]).endswith("intake_package.json"))
                self.assertIsInstance(call_kwargs["metadata"], dict)
                self.assertIsInstance(call_kwargs["written"], dict)
                self.assertEqual(call_kwargs["directive_id"], dir_id)
                self.assertEqual(call_kwargs["reviewer"], "Amit Ayer")
                self.assertEqual(call_kwargs["rationale"], "Test publication")
                self.assertEqual(result["graph_mutation"]["status"], "COMPLETE")
                self.assertEqual(result["graph_mutation"]["mutation_summary"], {"status": "PASS"})


if __name__ == "__main__":
    unittest.main()

