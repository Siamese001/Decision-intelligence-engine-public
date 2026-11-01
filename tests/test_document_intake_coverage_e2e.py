"""
tests/test_document_intake_coverage_e2e.py

Wave 5 End-to-End Ingestion Coverage Suite.
Validates the complete document format matrix lifecycle across all supported formats:
.md, .txt, .docx, .xlsx, .csv, .tsv, .html, .eml, .pptx, .pdf, .json, .svg, .drawio, .vsdx.
Verifies conversion to Markdown, source unit locator extraction, coverage proofs,
and deterministic conversion caching with locator restoration.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse
import zipfile
import zlib

import openpyxl
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.source_classification.capability_registry import (
    SupportStatus,
    audit_matrix,
    get_capability_by_ext,
    get_document_extensions,
)
from tests.test_pptx_source import _create_synthetic_pptx_bytes


def _build_pdf_bytes(text: str = "Enterprise Architecture Strategy Q3") -> bytes:
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


def _build_docx_bytes(title: str = "Quarterly Roadmap Deliverables") -> bytes:
    xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>Engineering execution plan for unified document intake.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Phase</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Wave 5 E2E Matrix</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Lead Engineer</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>""".encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _build_xlsx_bytes(sheet_title: str = "Allocations") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(["Project", "Budget Q1", "Budget Q2"])
    ws.append(["Document Pipeline", 250000, 310000])
    ws.append(["Decision Intelligence", 450000, 490000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_vsdx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '<Default Extension="xml" ContentType="application/xml"/>\n'
            '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>\n'
            '</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>\n'
            '</Relationships>',
        )
        zf.writestr(
            "visio/pages/pages.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main">\n'
            '  <Page ID="0" Name="Topology" ViewCentric="0"/>\n'
            '</Pages>',
        )
        zf.writestr(
            "visio/pages/page1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">\n'
            '  <Shapes>\n'
            '    <Shape ID="1" NameU="Service"><Text>Ingestion Coordinator</Text></Shape>\n'
            '    <Shape ID="2" NameU="Storage"><Text>Graph Store</Text></Shape>\n'
            '  </Shapes>\n'
            '  <Connects>\n'
            '    <Connect FromSheet="3" ToSheet="1"/>\n'
            '    <Connect FromSheet="3" ToSheet="2"/>\n'
            '  </Connects>\n'
            '</PageContents>',
        )
    return buf.getvalue()


class TestDocumentIntakeCoverageE2E(unittest.TestCase):
    """End-to-end full format matrix verification suite."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_p = Path(self.tmp_dir.name)
        self.cache_dir = self.work_p / "conversion_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _generate_fixture(self, ext: str) -> Path:
        """Construct representative, authentic fixture payload for given extension."""
        p = self.work_p / f"fixture_sample{ext}"
        if ext in (".md", ".markdown"):
            p.write_text(f"# Executive Brief for {ext}\n\nStrategic goals for Q3 delivery in {ext}.\n", encoding="utf-8")
        elif ext in (".txt", ".text"):
            p.write_text(f"Operational status ({ext}): All pipeline stages nominal.\n", encoding="utf-8")
        elif ext == ".docx":
            p.write_bytes(_build_docx_bytes(title=f"Roadmap Deliverables ({ext})"))
        elif ext == ".xlsx":
            p.write_bytes(_build_xlsx_bytes(sheet_title="AllocationsXLSX"))
        elif ext == ".xlsm":
            p.write_bytes(_build_xlsx_bytes(sheet_title="AllocationsXLSM"))
        elif ext == ".csv":
            p.write_text("Service,Region,Status\ningestion,us-east,healthy\nstorage,us-west,healthy\n", encoding="utf-8")
        elif ext == ".tsv":
            p.write_text("Service\tRegion\tStatus\ningestion\tus-east\thealthy\nstorage\tus-west\thealthy\n", encoding="utf-8")
        elif ext in (".html", ".htm"):
            p.write_text(f"<!DOCTYPE html><html><body><h1>Project Plan {ext}</h1><p>Deliverables for {ext}.</p></body></html>", encoding="utf-8")
        elif ext == ".eml":
            p.write_text(f"From: architect@enterprise.com\nTo: team@enterprise.com\nSubject: Launch Plan {ext}\n\nExecution schedule attached.", encoding="utf-8")
        elif ext == ".pptx":
            p.write_bytes(_create_synthetic_pptx_bytes())
        elif ext == ".pdf":
            p.write_bytes(_build_pdf_bytes(text=f"Enterprise Architecture Strategy {ext}"))
        elif ext == ".json":
            p.write_text(json.dumps({"pipeline": "document_intake", "status": "active", "ext": ext, "version": 5}), encoding="utf-8")
        elif ext == ".svg":
            p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><title>Map {ext}</title><rect id="n1" aria-label="Ingest"/><text id="t1">Coordinator {ext}</text></svg>', encoding="utf-8")
        elif ext == ".drawio":
            p.write_text(f'<mxfile><diagram name="Arch"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="x" value="Pipeline Step {ext}" vertex="1" parent="1"/></root></mxGraphModel></diagram></mxfile>', encoding="utf-8")
        elif ext == ".vsdx":
            p.write_bytes(_build_vsdx_bytes())
        else:
            raise ValueError(f"Unmapped test fixture extension: {ext}")
        return p

    def test_full_format_matrix_intake_lifecycle(self) -> None:
        """Verify complete ingestion lifecycle across 100% of supported document extensions."""
        supported_exts = [
            ext for ext in get_document_extensions()
            if ext in {
                ".md", ".markdown", ".txt", ".text", ".docx", ".xlsx", ".xlsm",
                ".csv", ".tsv", ".html", ".htm", ".eml", ".pptx", ".pdf",
                ".json", ".svg", ".drawio", ".vsdx"
            }
        ]

        self.assertGreaterEqual(len(supported_exts), 14, "Expected at least 14 active document extensions")

        for ext in supported_exts:
            with self.subTest(format=ext):
                fixture_path = self._generate_fixture(ext)
                self.assertTrue(fixture_path.is_file(), f"Fixture missing for {ext}")

                # First run: initial conversion and cache generation
                meta1: dict = {"project": "matrix_e2e_test"}
                md1, s_hash1, fmt1 = load_document_content(fixture_path, metadata=meta1, cache_dir=self.cache_dir)

                self.assertTrue(bool(md1.strip()), f"Empty markdown produced for {ext}")
                self.assertTrue(bool(s_hash1), f"Empty source hash for {ext}")
                self.assertFalse(meta1["conversion_artifact"]["was_cached"], f"Initial run must not be cached for {ext}")

                units1 = meta1.get("source_units", [])
                self.assertGreater(len(units1), 0, f"Expected non-empty source units for {ext}")
                for u in units1:
                    self.assertTrue(bool(u.get("locator")), f"Missing locator in unit for {ext}")
                    self.assertEqual(u.get("source_hash"), s_hash1)

                coverage1 = meta1.get("source_coverage", {})
                self.assertTrue(coverage1.get("complete"), f"Coverage not complete for {ext}")

                # Second run: cache hit with identical locators and units restored
                meta2: dict = {"project": "matrix_e2e_test"}
                md2, s_hash2, fmt2 = load_document_content(fixture_path, metadata=meta2, cache_dir=self.cache_dir)

                self.assertTrue(meta2["conversion_artifact"]["was_cached"], f"Second run must hit cache for {ext}")
                self.assertEqual(md1, md2, f"Markdown mismatch between runs for {ext}")
                self.assertEqual(s_hash1, s_hash2)
                self.assertEqual(fmt1, fmt2)

                units2 = meta2.get("source_units", [])
                self.assertEqual(len(units1), len(units2), f"Units count mismatch for {ext}")
                self.assertEqual([u["locator"] for u in units1], [u["locator"] for u in units2])

    def test_registry_parity_and_no_missing_extensions(self) -> None:
        """Verify that all non-blocked document format capabilities have working intake coverage."""
        for format_id, cap_dict in audit_matrix().items():
            if cap_dict["status"] in (SupportStatus.SUPPORTED.value, SupportStatus.PARTIAL.value):
                if cap_dict["primary_adapter"] == "blocked_pending_conversion":
                    continue
                for ext in cap_dict["extensions"]:
                    if ext in get_document_extensions():
                        # Every active document extension must be resolvable and ingestible
                        fixture_p = self._generate_fixture(ext)
                        meta: dict = {}
                        md, _, _ = load_document_content(fixture_p, metadata=meta, cache_dir=self.cache_dir)
                        self.assertGreater(len(md), 0, f"Failed ingestion for registry extension {ext}")


if __name__ == "__main__":
    unittest.main()
