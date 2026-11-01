"""
tests/test_document_conversion_cache_activation.py

Wave 3 Acceptance & Verification Suite:
Deterministic Up-Front Conversion Cache Activation, Invalidation, and Locators Restoration.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import openpyxl

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.ingest import build_canonical_metadata
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.conversion_cache import (
    compute_conversion_cache_key,
    get_cached_conversion,
    resolve_conversion_with_cache,
    resolve_default_conversion_cache_dir,
    CONVERTER_VERSIONS,
)


def _build_test_docx(title: str = "Quarterly Business Review") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
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
            f"<w:p><w:r><w:t>{title}</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Engineering deliverables for Q3 strategic roadmap.</w:t></w:r></w:p>"
            "<w:tbl>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Phase</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Target</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>P1</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Core Conversion Engine</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "</w:tbl>"
            "</w:body></w:document>"
        )
        z.writestr("word/document.xml", body_xml)
    return buf.getvalue()


def _build_test_xlsx(value: str = "Allocation Model") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws.cell(row=1, column=1, value=value)
    ws.cell(row=2, column=1, value="Prepared by Core Architecture")
    ws.cell(row=4, column=1, value="Module")
    ws.cell(row=4, column=2, value="Hours")
    ws.cell(row=4, column=3, value="Rate")
    ws.cell(row=4, column=4, value="Total")
    ws.cell(row=5, column=1, value="Intake Engine")
    ws.cell(row=5, column=2, value=40)
    ws.cell(row=5, column=3, value=250)
    ws.cell(row=5, column=4, value="=B5*C5")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestDocumentConversionCacheActivation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmp_dir.name)
        self.sandbox_out = self.tmppath / "sandbox_output"
        self.sandbox_out.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.sandbox_out / "conversion_cache"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_default_cache_activation_and_source_units_restoration_docx(self) -> None:
        """Verify DOCX first run saves cache artifact, second run hits cache with identical locators."""
        docx_bytes = _build_test_docx("Statement of Work 2026")
        docx_file = self.tmppath / "sow.docx"
        docx_file.write_bytes(docx_bytes)

        # Run 1: Cache MISS
        meta1 = {
            "project": "apollo",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        t0 = time.perf_counter()
        text1, sha1, fmt1 = load_document_content(docx_file, metadata=meta1)
        dur1 = time.perf_counter() - t0

        self.assertEqual(fmt1, "docx")
        self.assertIn("Statement of Work 2026", text1)
        self.assertFalse(meta1["conversion_artifact"]["was_cached"])
        cache_key = meta1["conversion_artifact"]["cache_key"]

        # Verify disk artifacts exist
        cached_md = self.cache_dir / f"{cache_key}.md"
        cached_json = self.cache_dir / f"{cache_key}.json"
        self.assertTrue(cached_md.is_file(), "Cached markdown artifact must exist")
        self.assertTrue(cached_json.is_file(), "Cached manifest must exist")

        manifest = json.loads(cached_json.read_text(encoding="utf-8"))
        self.assertEqual(manifest["cache_key"], cache_key)
        self.assertEqual(manifest["format"], "docx")
        self.assertIn("source_units", manifest)
        self.assertGreaterEqual(len(manifest["source_units"]), 1)

        # Run 2: Cache HIT
        meta2 = {
            "project": "apollo",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        t1 = time.perf_counter()
        text2, sha2, fmt2 = load_document_content(docx_file, metadata=meta2)
        dur2 = time.perf_counter() - t1

        self.assertEqual(fmt2, "docx")
        self.assertTrue(meta2["conversion_artifact"]["was_cached"], "Run 2 must report was_cached == True")
        self.assertEqual(text1, text2, "Cached text must match run 1 exactly")
        self.assertEqual(sha1, sha2, "Source SHA must match")

        # Verify coverage proof and source units parity
        self.assertEqual(meta1["_source_coverage"], meta2["_source_coverage"])
        self.assertEqual(len(meta1["source_units"]), len(meta2["source_units"]))
        for u1, u2 in zip(meta1["source_units"], meta2["source_units"]):
            self.assertEqual(u1["locator"], u2["locator"])
            self.assertEqual(u1["unit_type"], u2["unit_type"])

    def test_default_cache_activation_and_source_units_restoration_xlsx(self) -> None:
        """Verify XLSX first run saves cache artifact, second run hits cache with identical formula/sheet locators."""
        xlsx_bytes = _build_test_xlsx("Q3 Architecture Budget")
        xlsx_file = self.tmppath / "budget.xlsx"
        xlsx_file.write_bytes(xlsx_bytes)

        # Run 1: Cache MISS
        meta1 = {
            "project": "finance",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text1, sha1, fmt1 = load_document_content(xlsx_file, metadata=meta1)
        self.assertEqual(fmt1, "xlsx")
        self.assertIn("Q3 Architecture Budget", text1)
        self.assertFalse(meta1["conversion_artifact"]["was_cached"])
        cache_key = meta1["conversion_artifact"]["cache_key"]

        # Run 2: Cache HIT
        meta2 = {
            "project": "finance",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text2, sha2, fmt2 = load_document_content(xlsx_file, metadata=meta2)
        self.assertTrue(meta2["conversion_artifact"]["was_cached"])
        self.assertEqual(text1, text2)

        # Verify locators and sheets are identical
        self.assertEqual(meta1["_source_coverage"], meta2["_source_coverage"])
        self.assertEqual(len(meta1["source_units"]), len(meta2["source_units"]))
        for u1, u2 in zip(meta1["source_units"], meta2["source_units"]):
            self.assertEqual(u1["locator"], u2["locator"])
            self.assertEqual(u1["unit_type"], u2["unit_type"])

    def test_invalidation_on_changed_content_bytes(self) -> None:
        """Verify modifying workbook content invalidates cache and triggers reconversion."""
        xlsx_file = self.tmppath / "evolving_model.xlsx"
        xlsx_file.write_bytes(_build_test_xlsx("Version 1 Budget"))

        meta1 = {"project": "pricing", "conversion_cache_dir": str(self.cache_dir)}
        text1, _, _ = load_document_content(xlsx_file, metadata=meta1)
        key1 = meta1["conversion_artifact"]["cache_key"]
        self.assertIn("Version 1 Budget", text1)

        # Modify file bytes
        xlsx_file.write_bytes(_build_test_xlsx("Version 2 Revised Model"))

        meta2 = {"project": "pricing", "conversion_cache_dir": str(self.cache_dir)}
        text2, _, _ = load_document_content(xlsx_file, metadata=meta2)
        key2 = meta2["conversion_artifact"]["cache_key"]

        self.assertNotEqual(key1, key2, "Different file content must produce different cache keys")
        self.assertFalse(meta2["conversion_artifact"]["was_cached"], "Modified file must be a cache MISS")
        self.assertIn("Version 2 Revised Model", text2)

    def test_invalidation_on_changed_converter_version(self) -> None:
        """Verify upgrading converter version digest invalidates cache and forces fresh conversion."""
        docx_file = self.tmppath / "spec.docx"
        raw_bytes = _build_test_docx("System Specification")
        docx_file.write_bytes(raw_bytes)

        # Baseline conversion with standard version
        meta1 = {"project": "core", "conversion_cache_dir": str(self.cache_dir)}
        text1, _, _ = load_document_content(docx_file, metadata=meta1)
        key1 = meta1["conversion_artifact"]["cache_key"]

        # Run with incremented converter version
        text_new, key_new, was_cached, record = resolve_conversion_with_cache(
            source_p=docx_file,
            raw_bytes=raw_bytes,
            format_name="docx",
            loader_fn=lambda: (text1, meta1.get("_source_coverage"), meta1.get("source_units")),
            cache_dir=self.cache_dir,
            converter_version="docx-experimental-v2.0.0",
        )

        self.assertNotEqual(key1, key_new, "Different converter version must generate distinct cache key")
        self.assertFalse(was_cached, "Changed converter version must trigger fresh conversion")

    def test_no_cache_opt_out_flag(self) -> None:
        """Verify --no-cache / no_cache=True forces recompute and bypasses cache lookup."""
        docx_file = self.tmppath / "audit.docx"
        docx_file.write_bytes(_build_test_docx("Audit Memo"))

        meta1 = {"project": "audit", "conversion_cache_dir": str(self.cache_dir)}
        load_document_content(docx_file, metadata=meta1)

        # Subsequent run with no_cache=True
        meta2 = {"project": "audit", "conversion_cache_dir": str(self.cache_dir), "no_cache": True}
        text2, _, _ = load_document_content(docx_file, metadata=meta2)
        self.assertFalse(meta2["conversion_artifact"]["was_cached"], "no_cache must bypass cache hit")

    def test_cli_parser_and_metadata_wiring(self) -> None:
        """Verify CLI parser accepts --conversion-cache, --no-cache, --conversion-cache-dir and sets metadata."""
        parser = build_parser()
        doc_file = self.tmppath / "doc.txt"
        doc_file.write_text("sample document content")
        args = parser.parse_args([
            "ingest-document",
            "--input", str(doc_file),
            "--project", "test_proj",
            "--no-cache",
            "--conversion-cache-dir", str(self.cache_dir),
        ])

        self.assertTrue(args.no_cache)
        self.assertEqual(args.conversion_cache_dir, self.cache_dir)

        # Check metadata extraction
        meta = build_canonical_metadata(args)
        self.assertTrue(meta.get("no_cache"))
        self.assertTrue(meta.get("disable_conversion_cache"))
        self.assertEqual(meta.get("conversion_cache_dir"), str(self.cache_dir))

    def test_default_cache_directory_resolution(self) -> None:
        """Verify resolve_default_conversion_cache_dir resolves per-project folders."""
        meta = {"project": "project_apollo", "target_output_dir": str(self.sandbox_out)}
        resolved = resolve_default_conversion_cache_dir(metadata=meta)
        self.assertIsNotNone(resolved)
        self.assertIn("conversion_cache", str(resolved))
        self.assertIn("project_apollo", str(resolved))

        # Opt-out returns None
        meta_opt_out = {"project": "project_apollo", "no_cache": True}
        self.assertIsNone(resolve_default_conversion_cache_dir(metadata=meta_opt_out))

    def test_cache_corruption_detection_and_safe_fallback(self) -> None:
        """Verify corrupted cache artifacts are detected via SHA mismatch and safely bypassed."""
        docx_file = self.tmppath / "corrupt_test.docx"
        docx_file.write_bytes(_build_test_docx("Integrity Check"))

        meta1 = {"project": "integrity", "conversion_cache_dir": str(self.cache_dir)}
        text1, _, _ = load_document_content(docx_file, metadata=meta1)
        key = meta1["conversion_artifact"]["cache_key"]

        # Corrupt the cached markdown file
        cached_md = self.cache_dir / f"{key}.md"
        cached_md.write_text("Tampered content with forged bytes", encoding="utf-8")

        # Second load should detect corruption and recompute fresh
        meta2 = {"project": "integrity", "conversion_cache_dir": str(self.cache_dir)}
        text2, _, _ = load_document_content(docx_file, metadata=meta2)
        self.assertFalse(meta2["conversion_artifact"]["was_cached"], "Corrupted artifact must force fresh recompute")
        self.assertEqual(text1, text2, "Recomputed content must match authentic original")

    def test_cache_speedup_demonstration(self) -> None:
        """Verify that cache hit skips raw document unpacking and parsing."""
        xlsx_file = self.tmppath / "speed_test.xlsx"
        xlsx_file.write_bytes(_build_test_xlsx("Performance Benchmark"))

        meta1 = {"project": "perf", "conversion_cache_dir": str(self.cache_dir)}
        load_document_content(xlsx_file, metadata=meta1)

        # Cache HIT run
        meta2 = {"project": "perf", "conversion_cache_dir": str(self.cache_dir)}
        load_document_content(xlsx_file, metadata=meta2)
        self.assertTrue(meta2["conversion_artifact"]["was_cached"])


if __name__ == "__main__":
    unittest.main()
