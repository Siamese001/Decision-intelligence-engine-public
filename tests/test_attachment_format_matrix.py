"""
tests/test_attachment_format_matrix.py

Wave 1 Acceptance Suite: Compact Format-Adapter Contract & Preflight Matrix.
Tests deterministic preflight, locator grammars, visual escalation policies,
failure dispositions, and source unit generation across:
- .md, .txt, .docx, .pdf, .pptx, .xlsx, .csv, .tsv, .html, .eml
- Explicit blocking of legacy .ppt, .xls, .msg
- Explicit exclusion of audio (.wav) and transcript cues (.vtt) from document route
- Fail-closed blocking of unsupported binaries (.bin, .exe)
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.format_source_units import (
    FORMAT_ADAPTERS,
    FailureDisposition,
    FormatAdapterContract,
    PreflightResult,
    SourceUnit,
    VisualEscalationPolicy,
    get_format_adapter,
    preflight_attachment,
)
from dual_pipeline.document_pipeline_core.source_manifest import (
    build_source_manifest,
)
from dual_pipeline.exceptions import SourceIntegrityError
from dual_pipeline.source_classification.capability_registry import (
    SupportStatus,
    audit_matrix,
    get_capability_by_ext,
    get_capability_by_format_id,
    is_format_supported,
)


def _build_minimal_docx_bytes() -> bytes:
    xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Project SOW and Objectives</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("word/document.xml", xml)
    return bio.getvalue()


def _build_minimal_xlsx_bytes() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Allocations"
    ws.append(["Project", "Budget", "Owner"])
    ws.append(["Alpha", "150000", "Alice"])
    ws.append(["Beta", "250000", "Bob"])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _build_minimal_pptx_bytes() -> bytes:
    from tests.test_pptx_source import _create_synthetic_pptx_bytes
    return _create_synthetic_pptx_bytes()


def _get_minimal_pdf_bytes() -> bytes:
    from pypdf import PdfReader, PdfWriter
    p = Path("documents/hrit/HR360_PRF_Current_vs_Future_State.pdf")
    if p.exists():
        reader = PdfReader(str(p))
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        bio = io.BytesIO()
        writer.write(bio)
        return bio.getvalue()
    # Fallback to minimal PDF
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n"
        b"0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n162\n%%EOF\n"
    )


class TestAttachmentFormatMatrixDeclarations(unittest.TestCase):
    """Verify registry declarations, adapter contracts, and visual escalation policies."""

    def test_all_declared_formats_have_compact_adapter_contract(self) -> None:
        """Every supported format must declare unit, locator grammar, and visual escalation rule."""
        required_supported = [
            (".md", "TXT_MD", "L{line_number}", VisualEscalationPolicy.NEVER),
            (".txt", "TXT_MD", "L{line_number}", VisualEscalationPolicy.NEVER),
            (".docx", "DOCX", "p{paragraph_index}, tbl{table_index}!R{row}C{col}", VisualEscalationPolicy.NEVER),
            (".xlsx", "XLSX_WORKBOOK", "{sheet_name}!R{row}C{col}", VisualEscalationPolicy.NEVER),
            (".csv", "CSV_TSV", "R{row_index}", VisualEscalationPolicy.NEVER),
            (".tsv", "CSV_TSV", "R{row_index}", VisualEscalationPolicy.NEVER),
            (".html", "HTML_HTM", "block{block_index}", VisualEscalationPolicy.NEVER),
            (".eml", "EML", "header:{field_name}, body, attachment:{filename}", VisualEscalationPolicy.NEVER),
            (".pdf", "PDF", "p{page_num}", VisualEscalationPolicy.ON_LOW_TEXT_OR_SCANNED),
            (".pptx", "PPTX", "slide{slide_num}#shape{shape_id}", VisualEscalationPolicy.ON_NON_TEXT_SHAPES),
        ]

        for ext, fmt_id, expected_grammar, expected_vis in required_supported:
            cap = get_capability_by_ext(ext)
            self.assertIsNotNone(cap, f"Capability for {ext} should exist")
            self.assertEqual(cap.format_id, fmt_id)
            self.assertTrue(cap.is_attachment_supported, f"{ext} must be supported as document attachment")
            self.assertEqual(cap.locator_grammar, expected_grammar)
            self.assertEqual(cap.visual_escalation_rule, expected_vis.value)
            self.assertEqual(cap.failure_disposition, FailureDisposition.FAIL_CLOSED.value)

            # Check format_source_units adapter
            adapter = get_format_adapter(ext)
            self.assertIsNotNone(adapter, f"Adapter for {ext} must be registered")
            self.assertEqual(adapter.format_id, fmt_id)
            self.assertEqual(adapter.visual_policy, expected_vis)
            self.assertEqual(adapter.failure_disposition, FailureDisposition.FAIL_CLOSED)

    def test_legacy_formats_explicitly_blocked(self) -> None:
        """Legacy .ppt, .xls, and .msg must be explicitly blocked from generic decoding."""
        legacy_checks = [
            (".ppt", "PPT_LEGACY"),
            (".xls", "XLS_LEGACY"),
            (".msg", "MSG_LEGACY"),
        ]
        for ext, fmt_id in legacy_checks:
            adapter = get_format_adapter(ext)
            self.assertIsNotNone(adapter, f"Legacy adapter for {ext} must exist")
            self.assertEqual(adapter.visual_policy, VisualEscalationPolicy.BLOCKED)
            self.assertEqual(adapter.failure_disposition, FailureDisposition.BLOCKED)
            self.assertFalse(adapter.is_attachment_supported)
            self.assertFalse(is_format_supported(ext))

    def test_audio_and_cues_excluded_from_attachment_route(self) -> None:
        """Audio and transcript cues must remain outside the document attachment route."""
        excluded = [".wav", ".mp3", ".m4a", ".vtt", ".srt"]
        for ext in excluded:
            adapter = get_format_adapter(ext)
            self.assertIsNotNone(adapter, f"Adapter for {ext} must exist")
            self.assertFalse(adapter.is_attachment_supported)
            cap = get_capability_by_ext(ext)
            self.assertIsNotNone(cap)
            self.assertFalse(cap.is_attachment_supported)


class TestAttachmentPreflightAndEdgeCases(unittest.TestCase):
    """Adversarial and edge-case testing: boundary, malformed input, and bypass attempts."""

    def test_boundary_zero_byte_payloads_fail_closed(self) -> None:
        """Category: Boundary Payload — 0-byte attachments fail closed immediately."""
        formats_to_test = [".docx", ".xlsx", ".pdf", ".pptx", ".csv", ".tsv", ".html", ".eml", ".md", ".txt"]
        with tempfile.TemporaryDirectory() as tmpdir:
            for ext in formats_to_test:
                empty_file = Path(tmpdir) / f"empty{ext}"
                empty_file.write_bytes(b"")
                # Preflight check
                pf = preflight_attachment(b"", empty_file.name)
                self.assertFalse(pf.is_valid)
                self.assertEqual(pf.disposition, FailureDisposition.FAIL_CLOSED)
                self.assertEqual(pf.error_code, "EMPTY_PAYLOAD")

                # Loader check: must raise SourceIntegrityError
                with self.assertRaises(SourceIntegrityError):
                    load_document_content(empty_file)

    def test_malformed_container_rejection(self) -> None:
        """Category: Malformed Input — Corrupted non-container payloads fail closed."""
        cases = [
            ("corrupt.docx", b"THIS_IS_NOT_A_ZIP_DOCX", "MALFORMED_DOCX_CONTAINER"),
            ("corrupt.xlsx", b"THIS_IS_NOT_A_ZIP_XLSX", "MALFORMED_XLSX_CONTAINER"),
            ("corrupt.pptx", b"THIS_IS_NOT_A_ZIP_PPTX", "MALFORMED_PPTX_CONTAINER"),
            ("corrupt.pdf", b"NOT_A_PDF_FILE", "MALFORMED_PDF_HEADER"),
            ("corrupt.csv", b"Header1,Header2\nData,\x00\x01\x02binary", "MALFORMED_CSV_TSV_BINARY_NUL_BYTES"),
            ("corrupt.tsv", b"Header1\tHeader2\nData\t\x00\x01\x02binary", "MALFORMED_CSV_TSV_BINARY_NUL_BYTES"),
            ("corrupt.html", b"\x7fELF\x02\x01\x0100000000", "MALFORMED_HTML_BINARY_EXECUTABLE_SIGNATURE"),
            ("corrupt.md", b"\x7fELF\x02\x01\x0100000000", "BINARY_PAYLOAD_IN_TEXT_ROUTE"),
            ("corrupt.txt", b"MZ\x90\x00\x03\x00\x00\x00", "BINARY_PAYLOAD_IN_TEXT_ROUTE"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for fname, payload, expected_error in cases:
                p = Path(tmpdir) / fname
                p.write_bytes(payload)
                pf = preflight_attachment(payload, fname)
                self.assertFalse(pf.is_valid, f"{fname} should fail preflight")
                self.assertEqual(pf.error_code, expected_error)

                # Loader must raise ValueError fail-closed
                with self.assertRaises(ValueError) as ctx:
                    load_document_content(p)
                self.assertIn("preflight failed", str(ctx.exception).lower())

    def test_legacy_format_and_executable_bypass_blocked(self) -> None:
        """Category: Invariant Bypass Attempt — Legacy binaries and executables are blocked."""
        ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        bypass_cases = [
            ("old_deck.ppt", ole_header + b"sample_ppt_binary", "LEGACY_PPT_BLOCKED"),
            ("old_sheet.xls", ole_header + b"sample_xls_binary", "LEGACY_XLS_BLOCKED"),
            ("old_email.msg", ole_header + b"sample_msg_binary", "LEGACY_MSG_BLOCKED"),
            ("audio_leak.wav", b"RIFF....WAVEfmt ", "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED"),
            ("cue_leak.vtt", b"WEBVTT\n\n00:00.000 --> 00:05.000\nHello", "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED"),
            ("malware.exe", b"MZ\x90\x00executable_bytes", "UNSUPPORTED_BINARY_FORMAT"),
            ("library.dll", b"MZ\x90\x00dll_bytes", "UNSUPPORTED_BINARY_FORMAT"),
            ("payload.bin", b"\x00\x01\x02\x03random_bin", "UNSUPPORTED_BINARY_FORMAT"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for fname, payload, expected_code in bypass_cases:
                p = Path(tmpdir) / fname
                p.write_bytes(payload)
                pf = preflight_attachment(payload, fname)
                self.assertFalse(pf.is_valid)
                self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
                self.assertEqual(pf.error_code, expected_code)

                # Loader must fail closed without generic decoding
                with self.assertRaises(ValueError):
                    load_document_content(p)


class TestAttachmentExtractionAndSourceUnits(unittest.TestCase):
    """Verify successful multi-format extraction and source unit creation."""

    def test_csv_tsv_delimiter_header_row_units(self) -> None:
        """CSV and TSV must extract delimiter, header, and row units with R# locators."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. CSV test
            csv_path = Path(tmpdir) / "team.csv"
            csv_path.write_text("Name,Role,Location\nAlice,Architect,Boston\nBob,Engineer,Remote\n", encoding="utf-8")
            meta_csv: dict = {}
            text_csv, sha_csv, fmt_csv = load_document_content(csv_path, metadata=meta_csv)
            self.assertEqual(fmt_csv, "csv")
            self.assertIn("| R1 | Alice | Architect | Boston |", text_csv)
            self.assertIn("source_units", meta_csv)
            units = meta_csv["source_units"]
            self.assertEqual(len(units), 3)  # R0 header + R1 + R2
            self.assertEqual(units[0]["locator"], "R0")
            self.assertEqual(units[0]["unit_type"], "table_header")
            self.assertEqual(units[1]["locator"], "R1")
            self.assertEqual(units[1]["metadata"]["cells"]["Role"], "Architect")

            # 2. TSV test
            tsv_path = Path(tmpdir) / "team.tsv"
            tsv_path.write_text("ID\tStatus\tPriority\n101\tOpen\tHigh\n102\tClosed\tLow\n", encoding="utf-8")
            meta_tsv: dict = {}
            text_tsv, sha_tsv, fmt_tsv = load_document_content(tsv_path, metadata=meta_tsv)
            self.assertEqual(fmt_tsv, "tsv")
            self.assertIn("| R1 | 101 | Open | High |", text_tsv)
            self.assertIn("source_units", meta_tsv)
            tsv_units = meta_tsv["source_units"]
            self.assertEqual(tsv_units[0]["metadata"]["delimiter"], "\t")

    def test_html_dom_text_link_blocks(self) -> None:
        """HTML must project DOM blocks with headings, paragraphs, and link metadata."""
        html_content = (
            "<!DOCTYPE html><html><head><title>Architecture Spec</title></head><body>\n"
            "<h1>System Architecture</h1>\n"
            "<p>The platform delegates to <a href='https://example.com/engine'>Inference Engine</a>.</p>\n"
            "<h2>Security Guardrails</h2>\n"
            "<p>Egress is sanitized with zero credential leakage.</p>\n"
            "</body></html>"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            html_path = Path(tmpdir) / "spec.html"
            html_path.write_text(html_content, encoding="utf-8")
            meta: dict = {}
            text, sha, fmt = load_document_content(html_path, metadata=meta)
            self.assertEqual(fmt, "html")
            self.assertIn("# System Architecture", text)
            self.assertIn("Security Guardrails", text)
            self.assertIn("source_units", meta)
            units = meta["source_units"]
            self.assertGreaterEqual(len(units), 3)
            # Verify block locators and links
            block1 = next(u for u in units if u["locator"] == "block1")
            self.assertEqual(block1["metadata"]["tag"], "h1")
            block2 = next(u for u in units if u["locator"] == "block2")
            self.assertTrue(len(block2["metadata"]["links"]) > 0)
            self.assertEqual(block2["metadata"]["links"][0]["href"], "https://example.com/engine")

    def test_eml_header_body_attachment_units(self) -> None:
        """EML must parse message headers, body parts, and attachment manifests."""
        eml_content = (
            b"From: cto@enterprise.com\r\n"
            b"To: team@enterprise.com\r\n"
            b"Subject: Q3 Release Approval\r\n"
            b"Date: Wed, 01 Oct 2026 09:30:00 +0000\r\n"
            b"Message-ID: <msg-2026-1001@enterprise.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=\"boundary123\"\r\n\r\n"
            b"--boundary123\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Team, the Q3 release candidate is approved for production deployment.\r\n\r\n"
            b"--boundary123\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=\"audit_receipt.sig\"\r\n\r\n"
            b"RAW_SIGNATURE_PAYLOAD\r\n"
            b"--boundary123--\r\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            eml_path = Path(tmpdir) / "approval.eml"
            eml_path.write_bytes(eml_content)
            meta: dict = {}
            text, sha, fmt = load_document_content(eml_path, metadata=meta)
            self.assertEqual(fmt, "eml")
            self.assertIn("Subject: Q3 Release Approval", text)
            self.assertIn("Attachments: audit_receipt.sig", text)
            self.assertIn("source_units", meta)
            units = meta["source_units"]
            unit_locators = {u["locator"] for u in units}
            self.assertIn("header:Subject", unit_locators)
            self.assertIn("header:From", unit_locators)
            self.assertIn("body", unit_locators)
            self.assertIn("attachment:audit_receipt.sig", unit_locators)

    def test_native_docx_xlsx_pptx_pdf_extraction(self) -> None:
        """Native OOXML Word, Excel, PowerPoint, and PDF extract with valid provenance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. DOCX
            docx_path = Path(tmpdir) / "sow.docx"
            docx_path.write_bytes(_build_minimal_docx_bytes())
            meta_docx: dict = {}
            txt_docx, _, fmt_docx = load_document_content(docx_path, metadata=meta_docx)
            self.assertEqual(fmt_docx, "docx")
            self.assertIn("Project SOW and Objectives", txt_docx)

            # 2. XLSX
            xlsx_path = Path(tmpdir) / "budget.xlsx"
            xlsx_path.write_bytes(_build_minimal_xlsx_bytes())
            meta_xlsx: dict = {}
            txt_xlsx, _, fmt_xlsx = load_document_content(xlsx_path, metadata=meta_xlsx)
            self.assertEqual(fmt_xlsx, "xlsx")
            self.assertIn("Allocations!R2", txt_xlsx)
            self.assertIn("150000", txt_xlsx)

            # 3. PPTX
            pptx_path = Path(tmpdir) / "deck.pptx"
            pptx_path.write_bytes(_build_minimal_pptx_bytes())
            meta_pptx: dict = {}
            txt_pptx, _, fmt_pptx = load_document_content(pptx_path, metadata=meta_pptx)
            self.assertEqual(fmt_pptx, "pptx")
            self.assertIn("Slide 1", txt_pptx)

            # 4. PDF
            pdf_bytes = _get_minimal_pdf_bytes()
            pdf_path = Path(tmpdir) / "memo.pdf"
            pdf_path.write_bytes(pdf_bytes)
            meta_pdf: dict = {}
            txt_pdf, _, fmt_pdf = load_document_content(pdf_path, metadata=meta_pdf)
            self.assertEqual(fmt_pdf, "pdf")
            self.assertTrue(len(txt_pdf) > 0)


class TestAttachmentSourceManifestIntegration(unittest.TestCase):
    """Verify that build_source_manifest produces complete per-format unit accounting."""

    def test_source_manifest_accounting_across_format_matrix(self) -> None:
        """Source manifest must account for unit coverage across diverse format inputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files of diverse types
            md_p = Path(tmpdir) / "notes.md"
            md_p.write_text("# Meeting Notes\n- Action Item 1\n- Action Item 2\n", encoding="utf-8")

            csv_p = Path(tmpdir) / "rates.csv"
            csv_p.write_text("Tier,Rate\nSenior,250\nPrincipal,350\n", encoding="utf-8")

            docx_p = Path(tmpdir) / "charter.docx"
            docx_p.write_bytes(_build_minimal_docx_bytes())

            xlsx_p = Path(tmpdir) / "portfolio.xlsx"
            xlsx_p.write_bytes(_build_minimal_xlsx_bytes())

            sources = [md_p, csv_p, docx_p, xlsx_p]
            manifest = build_source_manifest(sources)

            self.assertEqual(manifest.total_sources, 4)
            entry_map = {e.filename: e for e in manifest.sources}

            # Check MD unit coverage
            md_entry = entry_map["notes.md"]
            self.assertIsNotNone(md_entry.unit_coverage)
            self.assertEqual(md_entry.unit_coverage.unit_type, "line_block")
            self.assertEqual(md_entry.unit_coverage.locator_pattern, "L{line_number}")

            # Check CSV unit coverage
            csv_entry = entry_map["rates.csv"]
            self.assertIsNotNone(csv_entry.unit_coverage)
            self.assertEqual(csv_entry.unit_coverage.unit_type, "delimited_row")
            self.assertEqual(csv_entry.unit_coverage.locator_pattern, "R{row_index}")

            # Check DOCX unit coverage
            docx_entry = entry_map["charter.docx"]
            self.assertIsNotNone(docx_entry.unit_coverage)
            self.assertEqual(docx_entry.unit_coverage.unit_type, "paragraph_table")

            # Check XLSX unit coverage
            xlsx_entry = entry_map["portfolio.xlsx"]
            self.assertIsNotNone(xlsx_entry.unit_coverage)
            self.assertEqual(xlsx_entry.unit_coverage.unit_type, "sheet_cell_row")
            self.assertEqual(xlsx_entry.unit_coverage.locator_pattern, "{sheet}!R{row}C{col}")


if __name__ == "__main__":
    unittest.main()
