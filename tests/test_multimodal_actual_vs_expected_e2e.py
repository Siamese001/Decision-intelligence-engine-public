"""tests/test_multimodal_actual_vs_expected_e2e.py

End-to-End Multi-Modal Test Suite with Itemized Actual vs. Expected Verifications.
Covers:
1. Audio (.wav) meeting ingestion -> Deliverable 01, 02, 03.
2. Raw Transcript (.txt) meeting dialogue -> Deliverable 01, 02, 03.
3. Written Documents (.md, .txt) -> Deliverable 03 only (01 & 02 strictly absent).
4. PDF Document (.pdf) -> Deliverable 03 only, multi-page extraction.
5. Word Document (.docx) -> Deliverable 03 only.
6. Notes Mode (CoPilot notes) -> Deliverable 02 and 03 (01 strictly absent).
7. Full Actual vs Expected Comparison Matrix generation and receipt capture.
"""
from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from tests.actual_vs_expected_harness import (
    ActualPipelineResult,
    ActualVsExpectedMatrix,
    ExpectedModalityContract,
)


class TestMultiModalActualVsExpectedE2E(unittest.TestCase):
    """Multi-Modal E2E suite verifying actual pipeline outputs against explicit expected contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.global_matrix = ActualVsExpectedMatrix()

    @classmethod
    def tearDownClass(cls) -> None:
        # Emit durable JSON receipt of all evaluations
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = reports_dir / "multimodal_actual_vs_expected_e2e_results.json"
        data = cls.global_matrix.to_dict()
        data["markdown_table"] = cls.global_matrix.to_markdown_table()
        receipt_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_audio_meeting_actual_vs_expected(self) -> None:
        """Verify Audio (.wav) ingestion produces Deliverables 01, 02, 03 and attributed speakers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wav_path = tmp / "nexus_review_audio.wav"
            with wave.open(str(wav_path), "w") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                # 1 second valid 16kHz PCM audio
                f.writeframes(struct.pack("<" + ("h" * 16000), *([0] * 16000)))

            out_dir = tmp / "output"
            pipeline = RawTranscriptPipeline(provider_type="offline")
            mock_transcriber = unittest.mock.MagicMock()
            mock_transcriber.transcribe.return_value = (
                "**Speaker 1** [00:00:00]: Good morning team, let's review the Nexus architecture deployment across all modalities. "
                "We need to ensure zero-lock storage concurrency and strict schema compliance before advancing.\n\n"
                "**Speaker 2** [00:01:00]: The zero-contention database connection manager and event bus are fully verified under stress. "
                "All isolated unit and integration test gates pass cleanly without lock timeouts.\n\n"
                "**Speaker 1** [00:02:00]: Apex Global engineering leadership formally approves the production rollout schedule and activation plan.\n"
            )
            with unittest.mock.patch("dual_pipeline.audio_ingestion.get_default_transcriber", return_value=mock_transcriber):
                res = pipeline.run(
                    input_path=str(wav_path),
                    metadata={
                        "meeting_name": "Nexus_Audio_Review",
                        "date": "09/17/2026",
                        "project": "project_nexus",
                        "author": "Amit Ayer",
                        "reviewer": "Amit Ayer",
                        "attendees": ["Amit Ayer", "Drew Oakley"],
                        "attendees_confirmed_by_operator": True,
                        "attendee_confirmation_receipt": {
                            "status": "RESOLVED_HITL",
                            "confirmed_by": "Amit Ayer",
                            "attendees": ["Amit Ayer", "Drew Oakley"],
                            "subject_sha256": "subj_nexus_audio_09172026",
                        },
                        "speaker_map": {
                            "Speaker 1": "Amit Ayer",
                            "Speaker 2": "Drew Oakley",
                        },
                        "roster": [
                            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                            {"name": "Drew Oakley", "org": "Apex Global", "role": "Technical Lead"},
                        ],
                        "duration_minutes": 5,
                    },
                    output_dir=str(out_dir),
                    package_only=True,
                )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="AUDIO (.wav)",
                input_filename="nexus_review_audio.wav",
                expected_deliverables={"01", "02", "03"},
                expected_speakers=["Amit Ayer", "Drew Oakley"],
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            # Assert actual vs expected
            self.assertEqual(actual.deliverables_found, {"01", "02", "03"})
            self.assertIn("Amit Ayer", actual.extracted_speakers)
            self.assertIn("Drew Oakley", actual.extracted_speakers)
            self.assertEqual(actual.f03_data.get("schema_version"), "5.7")
            self.assertEqual(actual.recap_date, "09/17/2026")

    def test_raw_transcript_meeting_actual_vs_expected(self) -> None:
        """Verify Raw Transcript text ingestion produces triad, reconciled speakers, and action items."""
        raw_text = (
            "**Cameron Mercer** [00:00:00]: Good morning team, let's align on Project Nexus architecture deployment across all modalities. "
            "We must enforce strict schema validation, monotonic progression, and zero-contention database concurrency.\n\n"
            "**Amit Ayer** [00:01:00]: The zero-contention database architecture and event bus are verified across all multi-worker stress harnesses. "
            "All pipeline stages maintain durable execution scope and isolated checkpoints.\n\n"
            "**Drew Oakley** [00:02:00]: Apex Global engineering formally approves the rollout schedule and phased regional activation checkpoints.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            in_file = tmp / "raw_nexus_sync.txt"
            in_file.write_text(raw_text, encoding="utf-8")

            out_dir = tmp / "output"
            pipeline = RawTranscriptPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(in_file),
                metadata={
                    "meeting_name": "Nexus_Architecture_Sync",
                    "date": "09/17/2026",
                    "project": "project_nexus",
                    "author": "Amit Ayer",
                    "reviewer": "Amit Ayer",
                    "attendees": ["Cameron Mercer", "Amit Ayer", "Drew Oakley"],
                    "roster": [
                        {"name": "Cameron Mercer", "org": "PartnerAdvisory"},
                        {"name": "Amit Ayer", "org": "PartnerAdvisory"},
                        {"name": "Drew Oakley", "org": "Apex Global"},
                    ],
                    "duration_minutes": 5,
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="RAW TRANSCRIPT (.txt)",
                input_filename="raw_nexus_sync.txt",
                expected_deliverables={"01", "02", "03"},
                expected_speakers=["Amit Ayer", "Drew Oakley", "Cameron Mercer"],
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"01", "02", "03"})
            self.assertTrue(all(s in actual.extracted_speakers for s in contract.expected_speakers))
            self.assertEqual(actual.recap_date, "09/17/2026")

    def test_document_markdown_actual_vs_expected(self) -> None:
        """Verify Markdown document ingestion produces Deliverable 03 ONLY (01 & 02 strictly absent)."""
        md_text = (
            "# Architecture Decision: Distributed Streaming Bus\n\n"
            "Date: 09/17/2026\n"
            "Author: Chief Architect\n\n"
            "## Context and Problem Statement\n"
            "The enterprise data ingestion platform requires high-throughput processing across multiple distributed cloud regions. "
            "Legacy uncoordinated database connections experience lock contention during peak concurrent batch ingestion windows.\n\n"
            "## Decision Outcome\n"
            "We decide to deploy the zero-lock concurrency event bus across all regions. "
            "Write-ahead logging (WAL mode) and serialized write channels are ratified as mandatory platform standards. "
            "PartnerAdvisory and Apex Global engineering approve the rollout schedule and phased regional activation plan.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md_file = tmp / "adr_streaming_bus.md"
            md_file.write_text(md_text, encoding="utf-8")

            out_dir = tmp / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(md_file),
                metadata={"title": "Streaming Bus ADR", "date": "09/17/2026", "project": "project_nexus", "author": "Amit Ayer", "reviewer": "Amit Ayer"},
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="DOCUMENT (Markdown .md)",
                input_filename="adr_streaming_bus.md",
                expected_deliverables={"03"},
                prohibited_deliverables={"01", "02"},
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"03"})
            self.assertNotIn("01", actual.deliverables_found)
            self.assertNotIn("02", actual.deliverables_found)
            self.assertEqual(actual.f03_data.get("schema_version"), "5.7")

    def test_document_text_actual_vs_expected(self) -> None:
        """Verify plain text memo ingestion enforces Deliverable 03 only."""
        memo_text = (
            "Executive Delivery Memo: Project Nexus Phase 1 Milestone Verification\n"
            "Date: 09/17/2026\n"
            "Author: Chief Architect\n\n"
            "PartnerAdvisory and Apex Global delivery leadership confirmed completion of Phase 1 architecture milestones. "
            "The core ingestion pipeline successfully verified zero-contention database concurrency and schema validation across all modalities. "
            "Performance benchmarks demonstrate sub-second parsing latency with zero data degradation under multi-worker execution loads. "
            "Apex Global engineering leadership formally approved production promotion for Phase 1 components.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            txt_file = tmp / "delivery_memo.txt"
            txt_file.write_text(memo_text, encoding="utf-8")

            out_dir = tmp / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(txt_file),
                metadata={"title": "Delivery Memo", "date": "09/17/2026", "project": "project_nexus", "author": "Amit Ayer", "reviewer": "Amit Ayer"},
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="DOCUMENT (Text .txt)",
                input_filename="delivery_memo.txt",
                expected_deliverables={"03"},
                prohibited_deliverables={"01", "02"},
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"03"})
            self.assertNotIn("01", actual.deliverables_found)
            self.assertNotIn("02", actual.deliverables_found)

    def test_document_pdf_actual_vs_expected(self) -> None:
        """Verify PDF document ingestion extracts pages and produces Deliverable 03 only."""
        pdf_path = ROOT / "inputs" / "rewire_earnings_2026" / "apex_global-q1-2026-earnings-script.pdf"
        self.assertTrue(pdf_path.exists(), "Test fixture PDF must exist")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(pdf_path),
                metadata={
                    "title": "Apex Global Q1 Earnings",
                    "date": "09/17/2026",
                    "project": "project_nexus",
                    "author": "Amit Ayer",
                    "reviewer": "Amit Ayer",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="DOCUMENT (PDF .pdf)",
                input_filename=pdf_path.name,
                expected_deliverables={"03"},
                prohibited_deliverables={"01", "02"},
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"03"})
            self.assertNotIn("01", actual.deliverables_found)
            self.assertNotIn("02", actual.deliverables_found)
            self.assertEqual(actual.f03_data.get("schema_version"), "5.7")

    def test_document_docx_actual_vs_expected(self) -> None:
        """Verify Word (.docx) document ingestion extracts OpenXML text and produces Deliverable 03 only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            docx_path = tmp / "nexus_charter.docx"
            docx_text = (
                "Project Nexus Governance Charter: Architecture Invariants and Operational Standards. "
                "Date: 09/17/2026. Author: Amit Ayer. Reviewer: Amit Ayer. "
                "This charter establishes mandatory architectural invariants across all ingestion modalities. "
                "The unified agentic pipeline enforces strict schema validation, zero-lock concurrency, "
                "evidence-preserving filtering, and fail-closed quality gates. "
                "All regional deployments must adhere to these ratified platform standards and governance protocols."
            )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f"<w:body><w:p><w:r><w:t>{docx_text}</w:t></w:r></w:p></w:body></w:document>",
                )
            docx_path.write_bytes(buf.getvalue())

            out_dir = tmp / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(docx_path),
                metadata={
                    "title": "Nexus Charter",
                    "date": "09/17/2026",
                    "project": "project_nexus",
                    "author": "Amit Ayer",
                    "reviewer": "Amit Ayer",
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="DOCUMENT (Word .docx)",
                input_filename=docx_path.name,
                expected_deliverables={"03"},
                prohibited_deliverables={"01", "02"},
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"03"})
            self.assertNotIn("01", actual.deliverables_found)
            self.assertNotIn("02", actual.deliverables_found)

    def test_notes_mode_boundary_actual_vs_expected(self) -> None:
        """Verify Notes Mode produces Deliverables 02 and 03 while strictly omitting Deliverable 01."""
        notes_text = (
            "# CoPilot Notes: Sprint Planning and Architecture Review\n\n"
            "**Attendees:** Amit Ayer, Cameron Mercer, Drew Oakley\n"
            "**Date:** 09/17/2026\n\n"
            "## Meeting Notes\n"
            "- Finalized API contracts for stream ingestion across all platform modalities.\n"
            "- PartnerAdvisory engineering committed to lead core pipeline deployment and multi-worker testing.\n"
            "- Apex Global delivery leadership verified zero-contention database concurrency benchmarks.\n"
            "- Ratified automated test coverage gates and established production promotion checkpoints.\n"
            "- All teams aligned on sprint velocity targets and architectural standards for Phase 1 release.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            notes_file = tmp / "copilot_notes.txt"
            notes_file.write_text(notes_text, encoding="utf-8")

            out_dir = tmp / "output"
            pipeline = RawTranscriptPipeline(provider_type="offline")
            res = pipeline.run(
                input_path=str(notes_file),
                metadata={
                    "meeting_name": "Sprint_Planning_Notes",
                    "date": "09/17/2026",
                    "project": "project_nexus",
                    "notes_mode": True,
                    "skip_output_1": True,
                    "author": "Amit Ayer",
                    "reviewer": "Amit Ayer",
                    "attendees": ["Amit Ayer", "Cameron Mercer", "Drew Oakley"],
                    "roster": [
                        {"name": "Amit Ayer", "org": "PartnerAdvisory"},
                        {"name": "Cameron Mercer", "org": "PartnerAdvisory"},
                        {"name": "Drew Oakley", "org": "Apex Global"},
                    ],
                    "duration_minutes": 5,
                },
                output_dir=str(out_dir),
                package_only=True,
            )

            actual = ActualPipelineResult.from_pipeline_run(res)
            contract = ExpectedModalityContract(
                modality="NOTES MODE (CoPilot Notes)",
                input_filename="copilot_notes.txt",
                expected_deliverables={"02", "03"},
                prohibited_deliverables={"01"},
                expected_qa_status="PASS",
            )

            self.global_matrix.evaluate(contract, actual)

            self.assertEqual(actual.deliverables_found, {"02", "03"})
            self.assertNotIn("01", actual.deliverables_found)
            self.assertEqual(actual.recap_date, "09/17/2026")

    def test_zz_actual_vs_expected_matrix_aggregation_and_reporting(self) -> None:
        """Verify the evaluation matrix recorded match status for every evaluated dimension."""
        matrix = self.global_matrix
        dict_report = matrix.to_dict()
        md_table = matrix.to_markdown_table()

        self.assertGreater(dict_report["total_evaluations"], 0)
        self.assertEqual(dict_report["mismatched_count"], 0)
        self.assertTrue(dict_report["all_matched"])
        self.assertIn("| Modality | Dimension / Metric |", md_table)
        matrix.assert_all_matched()


if __name__ == "__main__":
    unittest.main()
