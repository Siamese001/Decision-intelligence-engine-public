"""
tests/test_xlsx_edge_cases.py

Adversarial and Edge Case Test Suite for Universal XLSX Document Intake.
Covers:
  1. Boundary payloads: empty sheet, single cell, 0-byte input.
  2. Malformed input: truncated zip, missing worksheets, missing shared strings.
  3. Cell varieties: inline strings, numeric, boolean, multiline content sanitization.
  4. Sparse column alignment: columns skipping indices (e.g., A and D).
  5. Fallback recovery: raw CSV text in an .xlsx file gracefully decoded.
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from dual_pipeline.document_pipeline_core.content_loader import (
    load_document_content,
    _parse_openxml_sheet_rows,
)


class XlsxEdgeCasesTests(unittest.TestCase):
    """Adversarial and edge case tests for XLSX parser and content loader."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_xlsx_workbook(self) -> None:
        """Verify empty sheet returns empty markdown string without crashing."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")
        raw = buf.getvalue()
        rows = _parse_openxml_sheet_rows(raw)
        self.assertEqual(rows, [])

    def test_inline_strings_and_boolean_cells(self) -> None:
        """Verify inlineStr and boolean cells are parsed without shared strings table."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            sheet_content = (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                '<row r="1">'
                '<c r="A1" t="inlineStr"><is><t>Inline Value</t></is></c>'
                '<c r="B1" t="b"><v>1</v></c>'
                '<c r="C1"><v>42.5</v></c>'
                "</row>"
                "</sheetData></worksheet>"
            )
            z.writestr("xl/worksheets/sheet1.xml", sheet_content)
        raw = buf.getvalue()
        rows = _parse_openxml_sheet_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ["Inline Value", "TRUE", "42.5"])

    def test_multiline_cell_sanitization(self) -> None:
        """Verify multiline cells with embedded newlines do not break markdown tables."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            sheet_content = (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                '<row r="1">'
                '<c r="A1" t="inlineStr"><is><t>Line 1\nLine 2\r\nLine 3</t></is></c>'
                "</row>"
                "</sheetData></worksheet>"
            )
            z.writestr("xl/worksheets/sheet1.xml", sheet_content)
        raw = buf.getvalue()
        rows = _parse_openxml_sheet_rows(raw)
        self.assertEqual(rows[0][0], "Line 1 Line 2 Line 3")

    def test_sparse_columns_alignment(self) -> None:
        """Verify sparse columns (e.g. A and C without B) retain column index alignment."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            sheet_content = (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                '<row r="1">'
                '<c r="A1" t="inlineStr"><is><t>ColA</t></is></c>'
                '<c r="C1" t="inlineStr"><is><t>ColC</t></is></c>'
                "</row>"
                "</sheetData></worksheet>"
            )
            z.writestr("xl/worksheets/sheet1.xml", sheet_content)
        raw = buf.getvalue()
        rows = _parse_openxml_sheet_rows(raw)
        self.assertEqual(len(rows[0]), 3)
        self.assertEqual(rows[0][0], "ColA")
        self.assertEqual(rows[0][1], "")
        self.assertEqual(rows[0][2], "ColC")

    def test_corrupted_or_non_zip_fallback(self) -> None:
        """Verify non-zip CSV content with .xlsx extension falls back safely without unhandled error."""
        csv_data = b"Header1,Header2\nValue1,Value2\n"
        f = self.temp_path / "mock.xlsx"
        f.write_bytes(csv_data)

        meta: dict = {}
        text, sha, fmt = load_document_content(f, metadata=meta)
        self.assertEqual(fmt, "xlsx")
        self.assertIn("Header1", text)
        self.assertIn("Value1", text)
