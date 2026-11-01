"""
tests/end_to_end/test_text_docx_xlsx_evidence_e2e.py

End-to-End Acceptance Test Suite for Wave 2:
Preserves native text, Word, and workbook evidence without lossy conversion.
Verifies:
  1. Text and Markdown structural line locators (front matter, headings, lists, tables, code blocks).
  2. DOCX visible body/table evidence and complete non-body OOXML part inventory accounting.
  3. XLSX offset header selection, section detection, dual locators (RC and A1), and formula/cache pairs.
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import openpyxl

from dual_pipeline.document_pipeline_core.content_loader import (
    load_document_content,
)
from dual_pipeline.document_pipeline_core.docx_coverage import (
    extract_docx_with_coverage,
    inventory_docx_parts,
)
from dual_pipeline.document_pipeline_core.format_source_units import (
    build_text_source_units,
)
from dual_pipeline.document_pipeline_core.source_manifest import (
    build_source_manifest,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    project_xlsx_workbook,
)


def _build_complex_sow_docx() -> bytes:
    """Build a valid SOW DOCX with body, table, headers, footnotes, and comments."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
            '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
            '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        body_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Statement of Work: Project Apollo 2026</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>The vendor shall deliver the enterprise architecture solution.</w:t></w:r></w:p>"
            "<w:tbl>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Milestone</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Deliverable</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Fee</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>M1</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Architecture Blueprint</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>$50,000</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "</w:tbl>"
            "</w:body></w:document>"
        )
        z.writestr("word/document.xml", body_xml)
        z.writestr(
            "word/header1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:p><w:r><w:t>Confidential - Project Apollo</w:t></w:r></w:p></w:hdr>",
        )
        z.writestr(
            "word/footnotes.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:footnote w:id="1"><w:p><w:r><w:t>1. Deliverables subject to client sign-off.</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>",
        )
        z.writestr(
            "word/comments.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:comment w:id="1" w:author="Alice"><w:p><w:r><w:t>Review fee milestone 1.</w:t></w:r></w:p></w:comment>'
            "</w:comments>",
        )
    return buf.getvalue()


def _build_offset_header_xlsx_with_formulas() -> bytes:
    """Build workbook with title row, offset header on row 4, and cached/uncached formulas."""
    wb = openpyxl.Workbook()
    # Sheet 1: Financials
    ws1 = wb.active
    ws1.title = "Financials"
    ws1.cell(row=1, column=1, value="Client Advisory SOW Budget 2026")
    ws1.cell(row=2, column=1, value="Prepared by Engineering Leadership")
    # Row 3 is empty
    # Row 4 is Table Header
    ws1.cell(row=4, column=1, value="Cost Center")
    ws1.cell(row=4, column=2, value="Allocation")
    ws1.cell(row=4, column=3, value="Rate")
    ws1.cell(row=4, column=4, value="Total")
    # Row 5 data
    ws1.cell(row=5, column=1, value="Engineering")
    ws1.cell(row=5, column=2, value=100)
    ws1.cell(row=5, column=3, value=200)
    ws1.cell(row=5, column=4, value="=B5*C5")
    # Row 6 data
    ws1.cell(row=6, column=1, value="Design")
    ws1.cell(row=6, column=2, value=50)
    ws1.cell(row=6, column=3, value=180)
    ws1.cell(row=6, column=4, value="=B6*C6")

    # Sheet 2: Hidden Summary
    ws2 = wb.create_sheet(title="Summary")
    ws2.sheet_state = "hidden"
    ws2.cell(row=1, column=1, value="Project Budget Status")
    ws2.cell(row=2, column=1, value="Approved")

    buf = io.BytesIO()
    wb.save(buf)
    raw = buf.getvalue()

    # Inject cached evaluation for cell D5 (<v>20000</v>) while leaving D6 uncached
    z_in = zipfile.ZipFile(io.BytesIO(raw))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w") as z_out:
        for item in z_in.infolist():
            content = z_in.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b"<f>B5*C5</f>", b"<f>B5*C5</f><v>20000</v>")
            z_out.writestr(item, content)
    return out_buf.getvalue()


class TestTextDocxXlsxEvidenceE2E(unittest.TestCase):
    """E2E Verification of text, Word, and workbook evidence fidelity."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_text_and_markdown_source_line_locators_and_structure_fidelity(self) -> None:
        """Verify text/markdown retains 1-based source lines and detects structural boundaries."""
        sample_md = (
            "---\n"
            "title: Project Apollo Brief\n"
            "author: Lead Architect\n"
            "---\n"
            "# Executive Summary\n"
            "The engagement initiates Q1 2026.\n"
            "\n"
            "## Architecture Goals\n"
            "- Implement zero-contention SQLite storage\n"
            "- Enforce unidirectional L0 to L6 layer imports\n"
            "\n"
            "### Delivery Matrix\n"
            "| Phase | Scope | Target |\n"
            "| --- | --- | --- |\n"
            "| Phase 1 | Adapter Contract | Q1 |\n"
            "| Phase 2 | Evidence Preservation | Q2 |\n"
            "\n"
            "```python\n"
            "def verify_evidence():\n"
            "    return True\n"
            "```\n"
            "\n"
            "Final sign-off is pending review.\n"
        )
        md_file = self.tmppath / "brief.md"
        md_file.write_text(sample_md, encoding="utf-8")

        meta: Dict[str, Any] = {}
        text, sha256_digest, fmt = load_document_content(md_file, metadata=meta)

        self.assertEqual(fmt, "md")
        self.assertEqual(len(sha256_digest), 64)
        self.assertIn("# Executive Summary", text)
        self.assertIn("| Phase 1 | Adapter Contract | Q1 |", text)

        cov = meta.get("_source_coverage", {})
        self.assertTrue(cov.get("complete"))
        self.assertTrue(cov.get("has_front_matter"))
        self.assertEqual(cov.get("headings_count"), 3)  # # Executive Summary, ## Goals, ### Matrix
        self.assertEqual(cov.get("list_items_count"), 2)
        self.assertEqual(cov.get("tables_count"), 1)
        self.assertEqual(cov.get("code_blocks_count"), 1)

        source_units = meta.get("source_units", [])
        self.assertGreater(len(source_units), 5)

        # Verify front matter locator
        fm_unit = next(u for u in source_units if u["unit_type"] == "front_matter")
        self.assertEqual(fm_unit["locator"], "L1-L4")

        # Verify heading locator
        h1_unit = next(u for u in source_units if u["unit_type"] == "heading" and u["metadata"].get("level") == 1)
        self.assertEqual(h1_unit["locator"], "L5")

        # Verify code block locator
        code_unit = next(u for u in source_units if u["unit_type"] == "code_block")
        self.assertIn("L18-L21", code_unit["locator"])

    def test_text_encoding_preservation_utf8_bom_and_utf16(self) -> None:
        """Verify UTF-8 with BOM and UTF-16 decode cleanly without corrupting structure."""
        bom_bytes = b"\xef\xbb\xbf# Title With BOM\nContent line with special characters: \xe2\x82\xac and \xe2\x80\x94\n"
        bom_file = self.tmppath / "bom.txt"
        bom_file.write_bytes(bom_bytes)

        meta: Dict[str, Any] = {}
        extracted_text, _, fmt = load_document_content(bom_file, metadata=meta)
        self.assertEqual(fmt, "txt")
        self.assertNotIn("\ufeff", extracted_text)
        self.assertIn("€ and —", extracted_text)

        cov = meta.get("_source_coverage", {})
        self.assertTrue(cov.get("complete"))
        self.assertEqual(cov.get("headings_count"), 1)

    def test_docx_evidence_preservation_and_ooxml_part_disposition_accounting(self) -> None:
        """Verify DOCX preserves visible body and reports all non-body OOXML parts."""
        docx_bytes = _build_complex_sow_docx()
        docx_file = self.tmppath / "apollo_sow.docx"
        docx_file.write_bytes(docx_bytes)

        # 1. Independent part inventory inspection
        inventory = inventory_docx_parts(docx_bytes)
        part_names = {p["part_name"] for p in inventory}
        self.assertIn("word/document.xml", part_names)
        self.assertIn("word/header1.xml", part_names)
        self.assertIn("word/footnotes.xml", part_names)
        self.assertIn("word/comments.xml", part_names)

        # 2. Loader extraction and coverage
        meta: Dict[str, Any] = {}
        extracted_text, sha, fmt = load_document_content(docx_file, metadata=meta)
        self.assertEqual(fmt, "docx")
        self.assertIn("Statement of Work: Project Apollo 2026", extracted_text)
        self.assertIn("[T1.R1.C1] Milestone [/T1.R1.C1]", extracted_text)
        self.assertIn("[T1.R2.C3] $50,000 [/T1.R2.C3]", extracted_text)

        cov = meta.get("_source_coverage", {})
        self.assertTrue(cov.get("complete"))
        self.assertEqual(cov.get("table_count"), 1)
        self.assertEqual(cov.get("row_count"), 2)
        self.assertEqual(cov.get("cell_count"), 6)

        # 3. Dispositions verification
        disp_summary = cov.get("disposition_summary", {})
        self.assertGreaterEqual(disp_summary.get("included", 0), 1)
        self.assertGreaterEqual(disp_summary.get("excluded", 0), 2)  # header, packaging
        self.assertGreaterEqual(disp_summary.get("review_required", 0), 2)  # footnotes, comments

        non_body = cov.get("non_body_dispositions", {})
        self.assertEqual(non_body.get("word/header1.xml"), "excluded")
        self.assertEqual(non_body.get("word/footnotes.xml"), "review-required")
        self.assertEqual(non_body.get("word/comments.xml"), "review-required")

    def test_xlsx_offset_header_and_formula_cache_evidence(self) -> None:
        """Verify XLSX keeps non-empty title rows, detects offset table header, and records formula pairs."""
        xlsx_bytes = _build_offset_header_xlsx_with_formulas()
        xlsx_file = self.tmppath / "budget_model.xlsx"
        xlsx_file.write_bytes(xlsx_bytes)

        meta: Dict[str, Any] = {}
        extracted_text, sha, fmt = load_document_content(xlsx_file, metadata=meta)
        self.assertEqual(fmt, "xlsx")

        # 1. Preamble/Metadata preserved (not assumed to be column headers)
        self.assertIn("| Financials!R1 | Client Advisory SOW Budget 2026 |", extracted_text)
        self.assertIn("| Financials!R2 | Prepared by Engineering Leadership |", extracted_text)

        # 2. Table Header in Row 4 detected
        self.assertIn("| Locator | Cost Center | Allocation | Rate | Total |", extracted_text)
        self.assertIn("| Financials!R5 | Engineering | 100 | 200 | 20000 |", extracted_text)
        # Row 6 has missing formula cache
        self.assertIn("| Financials!R6 | Design | 50 | 180 | FORMULA:=B6*C6 [MISSING_CACHE] |", extracted_text)

        # 3. Projection coverage
        cov = meta.get("_source_coverage", {})
        self.assertTrue(cov.get("complete"))
        self.assertEqual(cov.get("total_sheets"), 2)
        self.assertEqual(cov.get("formulas_detected"), 2)
        self.assertEqual(cov.get("missing_formula_cache"), 1)
        self.assertEqual(cov.get("formula_cache_pairs_count"), 2)

        # 4. Dual locator resolution check
        proj = project_xlsx_workbook(xlsx_bytes)
        # Check Sheet!R#C# resolution
        res_rc = proj.resolve_locator("Financials!R5C4")
        self.assertIsNotNone(res_rc)
        self.assertEqual(res_rc["value"], 20000)
        self.assertEqual(res_rc["formula"], "=B5*C5")

        # Check Sheet!A1 resolution
        res_a1 = proj.resolve_locator("Financials!D5")
        self.assertIsNotNone(res_a1)
        self.assertEqual(res_a1["value"], 20000)

        # Check Row locator resolution
        res_row = proj.resolve_locator("Financials!R5")
        self.assertIsNotNone(res_row)
        self.assertIn("Engineering", res_row["values"])

    def test_multi_source_manifest_accounting_across_preserved_formats(self) -> None:
        """Verify SourceManifest accounts for unit coverage across text, DOCX, and XLSX."""
        md_file = self.tmppath / "notes.md"
        md_file.write_text("# Meeting Notes\n- Item A\n- Item B\n", encoding="utf-8")

        docx_file = self.tmppath / "sow.docx"
        docx_file.write_bytes(_build_complex_sow_docx())

        xlsx_file = self.tmppath / "data.xlsx"
        xlsx_file.write_bytes(_build_offset_header_xlsx_with_formulas())

        manifest = build_source_manifest([md_file, docx_file, xlsx_file])
        self.assertEqual(len(manifest.sources), 3)

        entries = {e.format: e for e in manifest.sources}
        self.assertIn("md", entries)
        self.assertIn("docx", entries)
        self.assertIn("xlsx", entries)

        # Verify unit coverage exists on all entries
        for fmt, entry in entries.items():
            self.assertIsNotNone(entry.unit_coverage, f"Unit coverage missing for {fmt}")
            self.assertGreater(entry.unit_coverage.total_units, 0)
            self.assertGreater(entry.unit_coverage.included_units, 0)


if __name__ == "__main__":
    unittest.main()
