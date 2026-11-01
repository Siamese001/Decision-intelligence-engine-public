"""
tests/test_ingestion_modality_routing_and_source_units_parity.py

Comprehensive verification and regression test suite for:
1. Ingestion modality routing across all 7 modalities:
   - Audio (.wav, .mp3, .m4a, .m4v, .mp4) -> AUDIO
   - Transcripts (.vtt, .srt, diarized text, JSON turns) -> MEETING_TRANSCRIPT
   - Notes (copilot notes, notes_mode) -> MEETING_NOTES
   - Documents (.pdf, .docx, .pptx, .xlsx, .csv, .tsv, .html, .eml, JSON payloads) -> DOCUMENT
2. Source-unit coverage parity:
   - PDF page units
   - DOCX paragraph and table_cell units with exact total_records accounting
   - PPTX slide units
   - XLSX sheet_cell_row and table_row units with dual locators
   - Structural text/notes line_block and heading units
3. Information loss regression prevention:
   - Audio video container (.mp4, .m4v) classification
   - JSON transcript ingestion
   - Meeting notes temporal resolution (e.g. 2-2:30 PM ET)
   - Gate 3.14 substantive transcript evidence notes mode exemption
   - Stage U0 roster inspection notes mode exemption
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict

import openpyxl
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.document_pipeline_core.content_loader import (
    _load_docx,
    _load_pdf,
    _load_pptx,
    _load_xlsx,
    load_document_content,
)
from dual_pipeline.document_pipeline_core.format_source_units import (
    build_docx_source_units,
    build_text_source_units,
)
from dual_pipeline.temporal_resolution import resolve_temporal_window
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)
from dual_pipeline.transcript_qa_core.gate3_evidence import (
    check_substantive_transcript_evidence,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary
from tests.test_pptx_source import _create_synthetic_pptx_bytes


def _build_test_pdf_bytes(text: str = "Quarterly Business Review and Architecture Roadmap") -> bytes:
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_obj)
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    t_stream = DecodedStreamObject()
    t_stream.set_data(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin1"))
    page[NameObject("/Contents")] = writer._add_object(t_stream)
    page[NameObject("/Resources")] = resources
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_test_docx_bytes() -> bytes:
    xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Project Architecture Deliverables</w:t></w:r></w:p>
    <w:p><w:r><w:t>The following milestones must be delivered on schedule:</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Milestone</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Wave 1 Migration</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Lead Engineer</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _build_test_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budgets"
    ws.append(["Category", "Q1 Allocation", "Q2 Allocation"])
    ws.append(["Infrastructure", 120000, 140000])
    ws.append(["Engineering", 350000, 380000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestIngestionModalityRouting(unittest.TestCase):
    """Test deterministic modality routing across all formats."""

    def test_audio_modality_routing(self) -> None:
        for ext in (".wav", ".mp3", ".m4a", ".m4v", ".mp4", ".flac", ".ogg"):
            path = Path(f"meeting_recording{ext}")
            modality = detect_modality(input_path=path)
            self.assertEqual(modality, CanonicalModality.AUDIO, f"Failed for {ext}")
            self.assertEqual(get_deliverables_for_modality(modality), ["01", "02", "03"])

    def test_transcript_modality_routing(self) -> None:
        for ext in (".vtt", ".srt", ".transcript"):
            path = Path(f"meeting_transcript{ext}")
            modality = detect_modality(input_path=path)
            self.assertEqual(modality, CanonicalModality.MEETING_TRANSCRIPT)
            self.assertEqual(get_deliverables_for_modality(modality), ["01", "02", "03"])

        # Spoken text transcript
        spoken_text = "### [00:00:15] Alice Smith:\nLet us review the architecture priorities.\n\n### [00:00:45] Bob Jones:\nAgreed."
        modality = detect_modality(content=spoken_text)
        self.assertEqual(modality, CanonicalModality.MEETING_TRANSCRIPT)

    def test_json_transcript_modality_routing(self) -> None:
        json_transcript = json.dumps({
            "meeting_id": "mtg-2026-09-14",
            "turns": [
                {"speaker": "Alice", "text": "Starting the review now.", "timestamp": "00:01:00"},
                {"speaker": "Bob", "text": "Sounds good.", "timestamp": "00:01:15"}
            ]
        })
        modality = detect_modality(input_path="teams_transcript.json", content=json_transcript)
        self.assertEqual(modality, CanonicalModality.MEETING_TRANSCRIPT)

    def test_meeting_notes_modality_routing(self) -> None:
        copilot_notes = (
            "Meeting Recap\n"
            "Here are the main action items and discussion highlights:\n"
            "- Architecture team approved SQLite WAL mode.\n"
            "- Concurrency stress tests passed with 16 workers.\n"
        )
        modality = detect_modality(content=copilot_notes)
        self.assertEqual(modality, CanonicalModality.MEETING_NOTES)
        self.assertEqual(get_deliverables_for_modality(modality), ["02", "03"])

        # Explicit notes metadata
        modality_meta = detect_modality(input_path="notes.txt", metadata={"notes_mode": True})
        self.assertEqual(modality_meta, CanonicalModality.MEETING_NOTES)

    def test_document_modality_routing(self) -> None:
        for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".tsv", ".html", ".eml"):
            path = Path(f"technical_spec{ext}")
            modality = detect_modality(input_path=path)
            self.assertEqual(modality, CanonicalModality.DOCUMENT, f"Failed for {ext}")
            self.assertEqual(get_deliverables_for_modality(modality), ["03"])

        # Structured non-transcript JSON
        doc_json = json.dumps({"schema": "decision-graph:1.0", "entity_upserts": [{"id": "sys:1"}]})
        modality = detect_modality(input_path="graph_delta.json", content=doc_json)
        self.assertEqual(modality, CanonicalModality.DOCUMENT)


class TestSourceUnitCoverageAndParity(unittest.TestCase):
    """Test source unit generation across document formats."""

    def test_pdf_source_units_generation(self) -> None:
        pdf_bytes = _build_test_pdf_bytes("Enterprise AI Strategy and Roadmap 2026")
        meta: Dict[str, Any] = {}
        text = _load_pdf(Path("strategy.pdf"), pdf_bytes, metadata=meta)
        self.assertIn("Enterprise AI Strategy", text)
        self.assertIn("source_units", meta)
        units = meta["source_units"]
        self.assertGreaterEqual(len(units), 1)
        self.assertEqual(units[0]["unit_type"], "page")
        self.assertEqual(units[0]["locator"], "p1")
        self.assertIn("char_count", units[0]["metadata"])

    def test_docx_source_units_generation(self) -> None:
        docx_bytes = _build_test_docx_bytes()
        meta: Dict[str, Any] = {}
        text = _load_docx(Path("project_sow.docx"), docx_bytes, metadata=meta)
        self.assertIn("Project Architecture Deliverables", text)
        self.assertIn("source_units", meta)
        units = meta["source_units"]
        self.assertGreaterEqual(len(units), 3)

        # Check paragraph unit
        p_units = [u for u in units if u["unit_type"] == "paragraph"]
        self.assertTrue(len(p_units) >= 2)
        self.assertEqual(p_units[0]["locator"], "p1")

        # Check table cell unit
        c_units = [u for u in units if u["unit_type"] == "table_cell"]
        self.assertTrue(len(c_units) >= 4)
        self.assertIn("T1.R1.C1", c_units[0]["locator"])

        # Check exact total_records accounting
        cov = meta["_source_coverage"]
        self.assertEqual(cov["total_records"], len(p_units) + len(c_units))

    def test_pptx_source_units_generation(self) -> None:
        pptx_bytes = _create_synthetic_pptx_bytes()
        meta: Dict[str, Any] = {}
        text = _load_pptx(Path("presentation.pptx"), pptx_bytes, metadata=meta)
        self.assertIn("source_units", meta)
        units = meta["source_units"]
        self.assertGreaterEqual(len(units), 1)
        self.assertEqual(units[0]["unit_type"], "slide")
        self.assertEqual(units[0]["locator"], "slide1")
        self.assertIn("paragraphs_count", units[0]["metadata"])

    def test_xlsx_source_units_generation(self) -> None:
        xlsx_bytes = _build_test_xlsx_bytes()
        meta: Dict[str, Any] = {}
        text = _load_xlsx(Path("financial_model.xlsx"), xlsx_bytes, metadata=meta)
        self.assertIn("Budgets", text)
        self.assertIn("source_units", meta)
        units = meta["source_units"]
        unit_types = {u["unit_type"] for u in units}
        self.assertTrue(bool(unit_types & {"table_row", "sheet_cell_row"}))

    def test_text_and_notes_source_units_generation(self) -> None:
        notes_text = (
            "---\n"
            "meeting: Architecture Review\n"
            "---\n"
            "# Key Decisions\n"
            "- Implement zero-contention SQLite storage.\n"
            "- Enforce unidirectional L0 to L6 layer imports.\n"
        )
        units = build_text_source_units(notes_text, "hash123", "md")
        unit_types = {u.unit_type for u in units}
        self.assertIn("front_matter", unit_types)
        self.assertIn("heading", unit_types)
        self.assertIn("list_item", unit_types)


class TestInformationLossRegression(unittest.TestCase):
    """Specific regression assertions ensuring zero information loss."""

    def test_audio_video_container_classification_regression(self) -> None:
        """Verify .m4v and .mp4 classify as audio rather than falling back to text."""
        for ext in (".m4v", ".mp4"):
            mod = classify_source_modality(Path(f"test_recording{ext}"))
            self.assertEqual(mod, SourceModality.AUDIO_ONLY_UNDIARIZED)

    def test_meeting_notes_temporal_window_regression(self) -> None:
        """Verify natural note timestamps like '2-2:30 PM ET' resolve cleanly."""
        sample_text = (
            "Team Sync Notes\n"
            "Time: 2-2:30 PM ET\n"
            "Discussed roadmap milestones."
        )
        res = resolve_temporal_window(raw_text=sample_text)
        self.assertNotEqual(res.display_window, "TIME_UNRESOLVED")
        self.assertIn("2-2:30 PM ET", res.display_window)
        self.assertEqual(res.resolution_source, "RAW_TRANSCRIPT_TEXT")

    def test_gate_3_14_substantive_transcript_notes_mode_exemption(self) -> None:
        """Verify notes mode does not require spoken dialogue turns in Gate 3.14."""
        data = {
            "evidence_index": {
                "ev-1": {"locator": {"kind": "line", "value": "L5"}, "exact_text": "Approved SQLite WAL mode"}
            }
        }
        # In notes mode with empty f1_text (Output 1 skipped), Gate 3.14 substantive check passes
        passed, failures = check_substantive_transcript_evidence(data, f1_text="", metadata={"notes_mode": True})
        self.assertTrue(passed, f"Unexpected failures: {failures}")
        self.assertEqual(len(failures), 0)

    def test_u0_roster_completeness_notes_mode_exemption(self) -> None:
        """Verify Stage U0 roster inspection does not block notes mode."""
        u0 = U0IngestionBoundary()
        meta = {
            "notes_mode": True,
            "attendees": ["Alice", "Bob"],
        }
        # Should not raise HITLRequiredError for unverified attendee roles
        u0._inspect_roster_completeness(meta)


if __name__ == "__main__":
    unittest.main()
