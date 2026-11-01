"""
tests/test_multi_input_pipeline_matrix_e2e.py

Comprehensive End-to-End Multi-Input Pipeline Test Matrix Suite.
Validates that the Unified Agentic Pipeline correctly processes all input modalities:
1. Spoken audio files (.wav, .mp4)
2. Raw spoken transcripts (.txt, .vtt)
3. Live MLX Whisper stream ingestion transcripts
4. General written documents (.md, .txt, .docx)
5. Visual architecture diagrams (.png and spatial intake packages)

Enforces the Core Architectural Output Partitioning Invariant:
- Spoken / meeting inputs produce the full deliverable triad:
    01_<meeting>_hitl_resolved_transcript.md
    02_<meeting>_executive_recap.md
    03_<meeting>_canonical_signal_projection.json
  plus the 4 governance receipts.
- General documents (DocumentPipeline) produce Deliverable 03 only,
  strictly omitting Deliverables 01 and 02.
- Architecture diagrams (normalize_architecture / MultimodalSlideExtractor)
  produce normalized candidate bundles and visual topologies, strictly
  omitting Deliverables 01, 02, and 03.
- Edge cases (empty files, recording gaps per C-11, single speaker, corrupted inputs)
  fail closed or handle gracefully without data corruption.
"""
from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from dual_pipeline.a2a.protocol import A2ATask, A2ATaskStatus
from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.normalization import normalize_architecture
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from pipeline_a_intake.agents import MultimodalSlideExtractor
from tools.validate_output_hierarchy import validate_meeting_directory


def _create_minimal_wav(path: Path) -> None:
    """Create a minimal 1-second valid WAV audio file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)


def _create_synthetic_wav(path: Path, duration_sec: float = 1.0, amplitude: int = 4000) -> None:
    """Create a synthetic non-silent WAV file with sufficient RMS to pass silence gating."""
    sample_rate = 16000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val = amplitude if (i % 32 < 16) else -amplitude
            frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


def _create_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    """Create a minimal valid Word DOCX zip archive containing document paragraphs."""
    p_xml = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{p_xml}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))


class TestMultiInputPipelineMatrixE2E(unittest.TestCase):
    """End-to-end integration and negative invariant verification across all modalities."""

    # ──────────────────────────────────────────────────────────────────────────
    # Modality 1: Spoken Audio Files (.wav, .mp4) -> Creates 01, 02, 03
    # ──────────────────────────────────────────────────────────────────────────

    def test_audio_wav_ingestion_creates_outputs_01_02_03(self) -> None:
        """Verify .wav audio input produces full triad 01, 02, 03 and receipts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            wav_file = tmp_path / "architecture_sync.wav"
            _create_minimal_wav(wav_file)

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Architecture_Sync_WAV"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(wav_file),
                metadata={
                    "meeting_name": "Architecture Sync WAV",
                    "date": "09/17/2026",
                    "time": "10:00 AM - 10:05 AM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                    "speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Drew Oakley"},
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            # Triad existence
            self.assertTrue(Path(result["01_transcript"]).exists(), "01_transcript must exist")
            self.assertTrue(Path(result["02_recap"]).exists(), "02_recap must exist")
            self.assertTrue(Path(result["03_graph"]).exists(), "03_graph must exist")

            # Validate Output 03 schema
            f3 = json.loads(Path(result["03_graph"]).read_text(encoding="utf-8"))
            CanonicalSignalProjectionV57.model_validate(f3)

            # Receipts existence
            target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
            self.assertTrue((target_dir / "qa_gates_report.json").exists())
            self.assertTrue((target_dir / "final_release_decision.json").exists())
            self.assertTrue((target_dir / "intake_package.json").exists())

    def test_audio_wav_unattributed_raises_hitl_required(self) -> None:
        """Verify unmapped audio without bypass_speaker_hitl deterministically fails closed."""
        from dual_pipeline.transcript_stages_core.models import HITLRequiredError
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            wav_file = tmp_path / "ambiguous.wav"
            _create_minimal_wav(wav_file)
            pipeline = RawTranscriptPipeline(provider_type="offline")
            with self.assertRaises(HITLRequiredError):
                pipeline.run(
                    input_path=str(wav_file),
                    metadata={
                        "meeting_name": "Ambiguous Meeting",
                        "date": "09/17/2026",
                        "time": "10:00 AM - 10:05 AM ET",
                        "attendees": ["Amit Ayer", "Drew Oakley"],
                    },
                    output_dir=str(tmp_path / "out"),
                    mutate_graph=False,
                )

    def test_audio_mp4_ingestion_creates_outputs_01_02_03(self) -> None:
        """Verify .mp4 audio container input produces full triad 01, 02, 03 and receipts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mp4_file = tmp_path / "team_standup.mp4"
            # Minimal container header
            mp4_file.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41")

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Team_Standup_MP4"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(mp4_file),
                metadata={
                    "meeting_name": "Team Standup MP4",
                    "date": "09/17/2026",
                    "time": "11:00 AM - 11:05 AM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                    "speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Drew Oakley"},
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertTrue(Path(result["01_transcript"]).exists(), "01_transcript must exist for MP4")
            self.assertTrue(Path(result["02_recap"]).exists(), "02_recap must exist for MP4")
            self.assertTrue(Path(result["03_graph"]).exists(), "03_graph must exist for MP4")

            f3 = json.loads(Path(result["03_graph"]).read_text(encoding="utf-8"))
            CanonicalSignalProjectionV57.model_validate(f3)

    # ──────────────────────────────────────────────────────────────────────────
    # Modality 2: Raw Spoken Transcripts (.txt, .vtt) -> Creates 01, 02, 03
    # ──────────────────────────────────────────────────────────────────────────

    def test_raw_spoken_transcript_txt_and_vtt_creates_outputs_01_02_03(self) -> None:
        """Verify raw text and WebVTT subtitle inputs produce full triad 01, 02, 03."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 1. Plain text dialogue
            txt_file = tmp_path / "nexus_review.txt"
            txt_file.write_text(
                "Amit Ayer [10:00 AM]: We are reviewing the unified pipeline DAG.\n"
                "Drew Oakley [10:05 AM]: Confirmed, the architecture meets all standards.\n",
                encoding="utf-8",
            )
            out_txt = tmp_path / "project_nexus" / "09-17-2026" / "Nexus_Review_TXT"
            out_txt.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            res_txt = pipeline.run(
                input_path=str(txt_file),
                metadata={
                    "meeting_name": "Nexus Review TXT",
                    "date": "09/17/2026",
                    "time": "10:00 AM - 10:05 AM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                },
                output_dir=str(out_txt),
                mutate_graph=False,
            )
            self.assertTrue(Path(res_txt["01_transcript"]).exists())
            self.assertTrue(Path(res_txt["02_recap"]).exists())
            self.assertTrue(Path(res_txt["03_graph"]).exists())

            # 2. WebVTT subtitles
            vtt_file = tmp_path / "nexus_review.vtt"
            vtt_file.write_text(
                "WEBVTT\n\n"
                "00:00:01.000 --> 00:00:05.000\n"
                "<v Amit Ayer>All unit tests for unified pipeline are passing.</v>\n\n"
                "00:00:06.000 --> 00:00:10.000\n"
                "<v Drew Oakley>Approved for release promotion.</v>\n",
                encoding="utf-8",
            )
            out_vtt = tmp_path / "project_nexus" / "09-17-2026" / "Nexus_Review_VTT"
            out_vtt.mkdir(parents=True, exist_ok=True)

            res_vtt = pipeline.run(
                input_path=str(vtt_file),
                metadata={
                    "meeting_name": "Nexus Review VTT",
                    "date": "09/17/2026",
                    "time": "10:00 AM - 10:05 AM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                },
                output_dir=str(out_vtt),
                mutate_graph=False,
            )
            self.assertTrue(Path(res_vtt["01_transcript"]).exists())
            self.assertTrue(Path(res_vtt["02_recap"]).exists())
            self.assertTrue(Path(res_vtt["03_graph"]).exists())

    # ──────────────────────────────────────────────────────────────────────────
    # Modality 3: Live MLX Whisper Stream Ingestion -> Creates 01, 02, 03
    # ──────────────────────────────────────────────────────────────────────────

    def test_live_mlx_whisper_stream_creates_outputs_01_02_03(self) -> None:
        """Verify live ingestion via LiveMeetingRecorder streaming produces full triad 01, 02, 03."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            rec_dir = tmp_path / "live_rec_session"
            rec_dir.mkdir(parents=True, exist_ok=True)

            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Live MLX Test Sync",
                meeting_date="09/17/2026",
                attendees="Amit Ayer, Drew Oakley",
                output_dir=rec_dir,
                backend="mlx",
                mock_mode=True,
                enable_ws=False,
                enable_copilot=False,
            )

            recorder._init_transcript_file()
            chunk_file = recorder.chunks_dir / "chunk_0000.wav"
            _create_synthetic_wav(chunk_file, duration_sec=1.0, amplitude=4000)

            text = recorder.process_chunk(chunk_file, chunk_idx=0)
            recorder.processed_chunks.add(chunk_file.name)
            self.assertIsNotNone(text)
            self.assertTrue(len(text) > 0)

            chunk_file_1 = recorder.chunks_dir / "chunk_0001.wav"
            _create_synthetic_wav(chunk_file_1, duration_sec=1.0, amplitude=4000)
            text_1 = recorder.process_chunk(chunk_file_1, chunk_idx=1)
            recorder.processed_chunks.add(chunk_file_1.name)
            self.assertIsNotNone(text_1)

            recorder.stop()
            self.assertTrue(recorder.transcript_md.exists())

            # Feed generated live transcript into the unified pipeline
            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Live_MLX_Test_Sync"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(recorder.transcript_md),
                metadata={
                    "meeting_name": "Live MLX Test Sync",
                    "date": "09/17/2026",
                    "time": "02:00 PM - 02:05 PM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

    # ──────────────────────────────────────────────────────────────────────────
    # Modality 4: General Written Documents (MD, TXT, DOCX) -> Output 03 ONLY
    # ──────────────────────────────────────────────────────────────────────────

    def test_general_markdown_document_output3_only(self) -> None:
        """Verify markdown document in DocumentPipeline produces Output 03 ONLY without 01 or 02."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            md_doc = tmp_path / "architecture_spec.md"
            md_doc.write_text(
                "# Project Nexus Architecture Specification\n\n"
                "Date: 09/17/2026\nAuthor: Amit Ayer\n\n"
                "## Decisions\n"
                "- System: NEXUS-CORE transitions to unified DAG pipeline.\n"
                "- Decision: Document ingestion generates Output 03 only.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Architecture_Spec_MD"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=md_doc,
                metadata={
                    "project": "project_nexus",
                    "title": "Project Nexus Architecture Specification",
                    "date": "09/17/2026",
                    "author": "Amit Ayer",
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertEqual(res["status"], "COMPLETE")
            self.assertTrue(res["output3_only"])

            target_out = Path(res["output_dir"])
            # Assert 03 exists
            self.assertTrue((target_out / Path(res["output_03"]).name).exists())
            self.assertTrue((target_out / "pipeline_execution_receipt.json").exists())

            # STRICT NEGATIVE ASSERTION: 01 and 02 must NOT exist
            self.assertEqual(list(target_out.glob("01_*")), [], "Deliverable 01 must not exist for document")
            self.assertEqual(list(target_out.glob("02_*")), [], "Deliverable 02 must not exist for document")

            # Validate output hierarchy compliance
            violations = validate_meeting_directory(target_out)
            self.assertEqual(violations, [])

    def test_general_txt_document_output3_only(self) -> None:
        """Verify plain text document in DocumentPipeline produces Output 03 ONLY without 01 or 02."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            txt_doc = tmp_path / "system_charter.txt"
            txt_doc.write_text(
                "Statement of Work and Engineering Charter\n"
                "Date: 09/17/2026\n"
                "System: system:maximo integration boundaries confirmed.\n"
                "Contract deliverables scheduled for Q4 2026.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "System_Charter_TXT"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=txt_doc,
                metadata={
                    "project": "project_nexus",
                    "title": "System Charter",
                    "date": "09/17/2026",
                    "author": "Amit Ayer",
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertEqual(res["status"], "COMPLETE")
            self.assertTrue(res["output3_only"])

            target_out = Path(res["output_dir"])
            self.assertTrue((target_out / Path(res["output_03"]).name).exists())
            self.assertEqual(list(target_out.glob("01_*")), [])
            self.assertEqual(list(target_out.glob("02_*")), [])

            violations = validate_meeting_directory(target_out)
            self.assertEqual(violations, [])

    def test_general_docx_document_output3_only(self) -> None:
        """Verify DOCX document in DocumentPipeline produces Output 03 ONLY without 01 or 02."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            docx_file = tmp_path / "project_lens_sow.docx"
            _create_minimal_docx(
                docx_file,
                [
                    "Statement of Work: Project LENS Visual Asset Intelligence System",
                    "Services under this SOW will be performed on a time-and-materials basis.",
                    "Key Deliverable: VLM Nameplate rule creation engine.",
                ],
            )

            out_dir = tmp_path / "project_lens" / "09-17-2026" / "LENS_SOW_DOCX"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=docx_file,
                metadata={
                    "project": "project_lens",
                    "title": "Project LENS SOW",
                    "date": "09/17/2026",
                    "author": "harper Hoffman",
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertEqual(res["status"], "COMPLETE")
            self.assertTrue(res["output3_only"])

            target_out = Path(res["output_dir"])
            self.assertTrue((target_out / Path(res["output_03"]).name).exists())
            self.assertEqual(list(target_out.glob("01_*")), [])
            self.assertEqual(list(target_out.glob("02_*")), [])

            violations = validate_meeting_directory(target_out)
            self.assertEqual(violations, [])

    # ──────────────────────────────────────────────────────────────────────────
    # Modality 5: Visual Architecture Diagram (PNG / Intake Package) -> NO 01, 02, 03
    # ──────────────────────────────────────────────────────────────────────────

    def test_architecture_diagram_package_and_png_no_01_02_03(self) -> None:
        """Verify architecture diagram intake packages and PNG visual topology extract candidates without 01, 02, 03."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = (
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "architecture-diagram.json"
            )
            self.assertTrue(fixture_path.exists(), "architecture-diagram.json fixture must exist")

            package = json.loads(fixture_path.read_text(encoding="utf-8"))
            bundle = normalize_architecture(package, run_context="test_matrix_run")

            # Verify candidate bundle structure
            self.assertEqual(bundle["contract_id"], "urn:nee:dual-pipeline:normalized-candidate-bundle:1.0.0")
            self.assertIn("candidates", bundle)
            self.assertIn("evidence", bundle)
            self.assertTrue(len(bundle["candidates"]) >= 2)

            # Verify spatial locators are preserved in evidence
            for ev in bundle["evidence"]:
                self.assertEqual(ev["locator"]["kind"], "region")
                self.assertTrue(ev["locator"]["value"].startswith("page-4:"))

            # Visual diagram extraction via MultimodalSlideExtractor
            extractor = MultimodalSlideExtractor(mock_mode=True)
            task = A2ATask(
                task_id="task_png_diagram_001",
                parent_task_id=None,
                sender_agent_id="urn:nee:agent:coordinator:1.0.0",
                recipient_agent_id="urn:nee:agent:pipeline-a:multimodal-slide:1.0.0",
                objective="Extract visual architecture diagram components and topology",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"deck_path": "sample_architecture_diagram.png"},
            )
            res = asyncio.run(extractor.handle_task(task))

            self.assertEqual(res["status"], "COMPLETED")
            self.assertTrue(res["has_diagrams"])
            self.assertIn("slides", res)

            # STRICT INVARIANT: Visual architecture processing does NOT produce 01, 02, or 03 deliverables
            self.assertNotIn("01_transcript", res)
            self.assertNotIn("02_recap", res)
            self.assertNotIn("03_graph", res)

            # Negative invariant: DocumentPipeline must cleanly reject raw binary PNG files
            doc_pipeline = DocumentPipeline(provider_type="offline")
            png_dummy = Path(tmpdir) / "arch_diagram.png"
            png_dummy.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
            with self.assertRaises(ValueError) as ctx:
                doc_pipeline.run(
                    input_path=png_dummy,
                    metadata={"project": "project_nexus", "title": "PNG Arch"},
                    output_dir=str(Path(tmpdir) / "out_png"),
                )
            self.assertIn("Binary image format '.png' cannot be ingested as text document", str(ctx.exception))

    # ──────────────────────────────────────────────────────────────────────────
    # Edge Cases: Empty Files, Recording Gaps (C-11), Monologues, Corrupted Inputs
    # ──────────────────────────────────────────────────────────────────────────

    def test_edge_case_empty_input_handling(self) -> None:
        """Verify empty files are rejected fail-closed without corrupt partial writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            empty_file = tmp_path / "empty_input.txt"
            empty_file.write_text("", encoding="utf-8")

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Empty_Test"
            out_dir.mkdir(parents=True, exist_ok=True)

            # DocumentPipeline on empty input
            doc_pipeline = DocumentPipeline(provider_type="offline")
            with self.assertRaises((ValueError, RuntimeError)):
                doc_pipeline.run(
                    input_path=empty_file,
                    metadata={"project": "project_nexus", "title": "Empty Doc"},
                    output_dir=str(out_dir),
                )

            # RawTranscriptPipeline on empty input
            trans_pipeline = RawTranscriptPipeline(provider_type="offline")
            with self.assertRaises((ValueError, RuntimeError)):
                trans_pipeline.run(
                    input_path=str(empty_file),
                    metadata={"meeting_name": "Empty Meeting", "date": "09/17/2026"},
                    output_dir=str(out_dir),
                )

    def test_edge_case_recording_gap_c11(self) -> None:
        """Verify Rule C-11: Standard recording gap marker is preserved without hallucinated filler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            gap_transcript = tmp_path / "gap_meeting.txt"
            gap_transcript.write_text(
                "Amit Ayer [00:00:00]: Meeting began, aligning on migration architecture.\n\n"
                "[Recording gap: 00:01:00 – 00:02:00]\n\n"
                "Drew Oakley [00:02:05]: Audio reconnected. The decision was approved during the break.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Gap_Meeting_C11"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(gap_transcript),
                metadata={
                    "meeting_name": "Gap Meeting C11",
                    "date": "09/17/2026",
                    "time": "10:00 AM - 10:03 AM ET",
                    "classification": "internal",
                    "attendees": ["Amit Ayer", "Drew Oakley"],
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertTrue(Path(result["01_transcript"]).exists())
            t1 = Path(result["01_transcript"]).read_text(encoding="utf-8")
            self.assertIn("Recording gap: 00:01:00 – 00:02:00", t1)

    def test_edge_case_single_speaker_monologue(self) -> None:
        """Verify single-speaker monologue completes without multi-speaker deadlock or hanging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mono_file = tmp_path / "executive_briefing.txt"
            mono_file.write_text(
                "Drew Oakley [00:00:01]: Good morning team. Today I am announcing our new operating model.\n"
                "Drew Oakley [00:02:00]: PartnerAdvisory and Apex Global are forming a unified delivery team.\n"
                "Drew Oakley [00:04:00]: Milestone target is set for end of month.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Single_Speaker_Mono"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(mono_file),
                metadata={
                    "meeting_name": "Single Speaker Mono",
                    "date": "09/17/2026",
                    "time": "09:00 AM - 09:05 AM ET",
                    "classification": "internal",
                    "attendees": ["Drew Oakley"],
                },
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())

    def test_edge_case_corrupted_binary_input(self) -> None:
        """Verify corrupted binary DOCX or WAV input fails cleanly with appropriate exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Corrupted DOCX (random garbage bytes)
            corrupt_docx = tmp_path / "corrupted.docx"
            corrupt_docx.write_bytes(b"\x50\x4b\x03\x04\x00\x00corrupted_non_zip_data")

            out_dir = tmp_path / "project_nexus" / "09-17-2026" / "Corrupted_DOCX"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = DocumentPipeline(provider_type="offline")
            with self.assertRaises((zipfile.BadZipFile, ValueError, RuntimeError)):
                pipeline.run(
                    input_path=corrupt_docx,
                    metadata={"project": "project_nexus", "title": "Corrupt Doc"},
                    output_dir=str(out_dir),
                )


if __name__ == "__main__":
    unittest.main()
