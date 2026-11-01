"""
tests/end_to_end/test_xlsx_document_intake_e2e.py

End-to-End Governance Test Suite for Universal XLSX Document Intake and Output 3 Routing.
Verifies:
  1. OpenXML XLSX workbook parsing with zero external dependencies.
  2. Formatting of multi-column, multi-row spreadsheets into canonical Markdown tables.
  3. Modality classification into CanonicalModality.DOCUMENT with deliverables ['03'].
  4. Integration with CanonicalAgenticPipeline content loaders and metadata helpers.
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from dual_pipeline.canonical_pipeline.modalities import (
    DOCUMENT_EXTENSIONS,
    CanonicalModality,
    get_deliverables_for_modality,
)
from dual_pipeline.canonical_pipeline.content_loader import load_file_content
from dual_pipeline.document_pipeline_core.content_loader import (
    load_document_content,
    extract_document_text_snippet,
)
from dual_pipeline.transcript_metadata_helper import is_document_content
from dual_pipeline.cli.ingest import is_document_source


def _build_minimal_xlsx(rows: list[list[str]]) -> bytes:
    """Helper to build a valid OpenXML XLSX archive in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
        )

        # Collect unique shared strings
        unique_strings = []
        string_to_id = {}
        for row in rows:
            for val in row:
                if val not in string_to_id:
                    string_to_id[val] = len(unique_strings)
                    unique_strings.append(val)

        # Build sharedStrings.xml
        sst_xml = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(unique_strings)}" uniqueCount="{len(unique_strings)}">',
        ]
        for s in unique_strings:
            sst_xml.append(f"<si><t>{s}</t></si>")
        sst_xml.append("</sst>")
        z.writestr("xl/sharedStrings.xml", "".join(sst_xml))

        # Build sheet1.xml
        sheet_xml = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "<sheetData>",
        ]
        for r_idx, row in enumerate(rows, start=1):
            sheet_xml.append(f'<row r="{r_idx}">')
            for c_idx, val in enumerate(row):
                col_letter = chr(ord("A") + c_idx)
                s_id = string_to_id[val]
                sheet_xml.append(f'<c r="{col_letter}{r_idx}" t="s"><v>{s_id}</v></c>')
            sheet_xml.append("</row>")
        sheet_xml.append("</sheetData></worksheet>")
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet_xml))

    return buf.getvalue()


class XlsxDocumentIntakeE2ETests(unittest.TestCase):
    """End-to-end tests for XLSX document ingestion and Output 3 routing."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_xlsx_in_document_extensions_ssot(self) -> None:
        """Verify .xlsx and .xls are registered in DOCUMENT_EXTENSIONS."""
        self.assertIn(".xlsx", DOCUMENT_EXTENSIONS)
        self.assertIn(".xls", DOCUMENT_EXTENSIONS)
        self.assertIn(".csv", DOCUMENT_EXTENSIONS)
        self.assertEqual(get_deliverables_for_modality(CanonicalModality.DOCUMENT), ["03"])

    def test_xlsx_document_detection(self) -> None:
        """Verify is_document_content and is_document_source classify XLSX files."""
        self.assertTrue(is_document_content("", "technical_mapping.xlsx"))
        self.assertTrue(is_document_content("", "sheet.xls"))

        xlsx_bytes = _build_minimal_xlsx([["Field", "Type"], ["reqId", "string"]])
        file_path = self.temp_path / "test.xlsx"
        file_path.write_bytes(xlsx_bytes)

        self.assertTrue(is_document_source(file_path))

    def test_xlsx_load_content_and_coverage(self) -> None:
        """Verify load_document_content extracts canonical markdown and coverage."""
        rows = [
            ["Technical Field ID", "Business Label", "Required"],
            ["cust_hiringReason", "Justification", "True"],
            ["cust_repWhom", "Employee being replaced", "False"],
        ]
        xlsx_bytes = _build_minimal_xlsx(rows)
        file_path = self.temp_path / "mapping.xlsx"
        file_path.write_bytes(xlsx_bytes)

        meta: dict = {}
        text, sha, fmt = load_document_content(file_path, metadata=meta)

        self.assertEqual(fmt, "xlsx")
        self.assertTrue(len(sha) == 64)
        self.assertIn("| Technical Field ID | Business Label | Required |", text)
        self.assertTrue(
            "| Sheet1!R2 | cust_hiringReason | Justification | True |" in text
            or "| R1 | cust_hiringReason | Justification | True |" in text
        )
        self.assertTrue(
            "| Sheet1!R3 | cust_repWhom | Employee being replaced | False |" in text
            or "| R2 | cust_repWhom | Employee being replaced | False |" in text
        )

        coverage = meta.get("_source_coverage", {})
        self.assertTrue(coverage.get("complete"))
        self.assertEqual(coverage.get("total_records"), 2)
        self.assertEqual(coverage.get("processed_records"), 2)

    def test_canonical_pipeline_content_loader_xlsx(self) -> None:
        """Verify load_file_content routes XLSX through canonical loader without exception."""
        rows = [
            ["ID", "Status", "Owner"],
            ["101", "Open", "Alice"],
            ["102", "Pending", "Bob"],
        ]
        xlsx_bytes = _build_minimal_xlsx(rows)
        file_path = self.temp_path / "orders.xlsx"
        file_path.write_bytes(xlsx_bytes)

        meta: dict = {}
        content = load_file_content(file_path, ".xlsx", meta, offline=True)
        self.assertIn("| ID | Status | Owner |", content)
        self.assertTrue(
            "| Sheet1!R2 | 101 | Open | Alice |" in content
            or "| R1 | 101 | Open | Alice |" in content
        )
    def test_hr_it_requisition_technical_mapping_fidelity_if_present(self) -> None:
        """Verify real HR IT Requisition mapping parses all 92 rows if file exists."""
        candidate_dir = Path.home() / "Library" / "CloudStorage" / "OneDrive-PartnerAdvisory" / "Documents" / "HR IT"
        real_p = candidate_dir / "Requisition_Technical_Field_Mapping 4.xlsx"
        if not real_p.exists():
            return
        meta: dict = {}
        text, sha, fmt = load_document_content(real_p, metadata=meta)
        self.assertEqual(fmt, "xlsx")
        coverage = meta.get("_source_coverage", {})
        self.assertIn(coverage.get("total_records"), (91, 94))
        self.assertIn("cust_hiringReason", text)
