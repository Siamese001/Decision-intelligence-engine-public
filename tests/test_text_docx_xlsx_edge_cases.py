"""
tests/test_text_docx_xlsx_edge_cases.py

Adversarial and Edge-Case Test Suite for Wave 2 Native Text, DOCX, and XLSX Intake.
Covers Mandatory Governance Categories:
  1. Boundary Payload: Empty sheets, isolated single cell, blank lines, minimal valid containers.
  2. Malformed Input: Unclosed front matter, unclosed code block, missing formula cache.
  3. Invariant Bypass Attempt: Forged locators fail closed, strict vs review-required OOXML chunks.
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict

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
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    project_xlsx_workbook,
)


class TestTextDocxXlsxEdgeCases(unittest.TestCase):
    """Adversarial and edge-case verification for Wave 2 evidence preservation."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    # --- Category 1: Boundary Payload ---

    def test_boundary_empty_and_whitespace_only_text(self) -> None:
        """Category: Boundary Payload — Empty or whitespace-only text produces zero units."""
        units_empty = build_text_source_units("", "dummy_hash")
        self.assertEqual(len(units_empty), 0)

        units_ws = build_text_source_units("\n   \n\t\n  \n", "dummy_hash")
        self.assertEqual(len(units_ws), 0)

    def test_boundary_xlsx_empty_sheet_and_isolated_distant_cell(self) -> None:
        """Category: Boundary Payload — Empty sheet renders placeholder; isolated cell is preserved."""
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "EmptySheet"

        ws2 = wb.create_sheet(title="DistantCell")
        ws2.cell(row=50, column=10, value="Isolated Value")

        buf = io.BytesIO()
        wb.save(buf)
        raw_bytes = buf.getvalue()

        proj = project_xlsx_workbook(raw_bytes)
        self.assertEqual(len(proj.all_sheet_names), 2)
        # Empty sheet has placeholder
        self.assertIn("*(Empty Sheet)*", proj.rendered_markdown)
        # Distant cell is preserved with both locators
        res_rc = proj.resolve_locator("DistantCell!R50C10")
        self.assertIsNotNone(res_rc)
        self.assertEqual(res_rc["value"], "Isolated Value")

        res_a1 = proj.resolve_locator("DistantCell!J50")
        self.assertIsNotNone(res_a1)
        self.assertEqual(res_a1["value"], "Isolated Value")

    # --- Category 2: Malformed Input ---

    def test_malformed_text_unclosed_front_matter_and_code_block(self) -> None:
        """Category: Malformed Input — Unclosed front matter or code blocks consume safely without crash."""
        unclosed_fm = "---\ntitle: Unclosed\nkey: val\n# Real Heading\nContent"
        units_fm = build_text_source_units(unclosed_fm, "hash1")
        # Since front matter is never closed, lines fall through to standard units
        self.assertFalse(any(u.unit_type == "front_matter" for u in units_fm))
        self.assertTrue(any(u.unit_type == "heading" for u in units_fm))

        unclosed_code = "```python\ndef foo():\n    return 42\n"
        units_code = build_text_source_units(unclosed_code, "hash2")
        self.assertTrue(any(u.unit_type == "code_block" for u in units_code))
        code_u = next(u for u in units_code if u.unit_type == "code_block")
        self.assertIn("L1-L3", code_u.locator)

    def test_malformed_formula_missing_cache_tagging(self) -> None:
        """Category: Malformed Input — Workbook with formula but no calculated cache is flagged."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "=COMPLEX_FINANCIAL_FORMULA(B1:B10)"

        buf = io.BytesIO()
        wb.save(buf)
        raw_bytes = buf.getvalue()

        proj = project_xlsx_workbook(raw_bytes)
        self.assertEqual(proj.formulas_detected, 1)
        self.assertEqual(proj.missing_formula_cache, 1)
        self.assertIn("MISSING_CACHE", proj.rendered_markdown)

    # --- Category 3: Invariant Bypass Attempt ---

    def test_invariant_bypass_forged_locator_returns_none(self) -> None:
        """Category: Invariant Bypass Attempt — Non-existent or forged cell locators fail closed."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "Real Value"
        buf = io.BytesIO()
        wb.save(buf)

        proj = project_xlsx_workbook(buf.getvalue())
        # Forged cell outside sheet
        self.assertIsNone(proj.resolve_locator("Sheet!R999C999"))
        self.assertIsNone(proj.resolve_locator("NonExistentSheet!A1"))
        self.assertIsNone(proj.resolve_locator("MALICIOUS;INJECTION"))

    def test_docx_external_altchunk_strict_rejection_vs_review_allowance(self) -> None:
        """Category: Invariant Bypass Attempt — DOCX altChunk fails strict mode; marks review in audited mode."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Visible</w:t></w:r></w:p><w:altChunk/></w:body></w:document>',
            )
        raw_bytes = buf.getvalue()

        # Strict mode must fail-closed with ValueError
        with self.assertRaisesRegex(ValueError, "compatible extractor"):
            extract_docx_with_coverage(raw_bytes, allow_review_required=False)

        # Audited mode with review allowance succeeds and marks altChunk as review-required
        text, cov = extract_docx_with_coverage(raw_bytes, allow_review_required=True)
        self.assertIn("Visible", text)
        self.assertEqual(cov["non_body_dispositions"].get("inline:altChunk"), "review-required")
        self.assertGreaterEqual(cov["disposition_summary"]["review_required"], 1)


if __name__ == "__main__":
    unittest.main()
