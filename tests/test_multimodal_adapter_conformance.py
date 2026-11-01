#!/usr/bin/env python3
"""Unified conformance and boundary test suite across all multi-modal source extractors:
PowerPoint (.pptx), Word (.docx), PDF (.pdf), and HTML (.html).
"""
from __future__ import annotations

import unittest

from dual_pipeline.docx_source import extract_docx_markdown
from dual_pipeline.html_source import MAX_HTML_BYTES, extract_html_markdown
from dual_pipeline.pdf_source import MAX_PDF_BYTES, extract_pdf_pages
from dual_pipeline.pptx_source import PPTXTable, extract_pptx_markdown


class MultimodalAdapterConformanceTests(unittest.TestCase):
    """Test suite ensuring all source-bound adapters maintain uniform behavior and boundary guarantees."""

    def test_all_adapters_reject_empty_bytes(self) -> None:
        """Every adapter must reject empty byte payloads cleanly with ValueError."""
        with self.assertRaises(ValueError) as ctx_pptx:
            extract_pptx_markdown(b"")
        self.assertIn("empty", str(ctx_pptx.exception).lower())

        with self.assertRaises(ValueError) as ctx_docx:
            extract_docx_markdown(b"")
        self.assertIn("empty", str(ctx_docx.exception).lower())

        with self.assertRaises(ValueError) as ctx_pdf:
            extract_pdf_pages(b"")
        self.assertIn("empty", str(ctx_pdf.exception).lower())

        with self.assertRaises(ValueError) as ctx_html:
            extract_html_markdown(b"")
        self.assertIn("empty", str(ctx_html.exception).lower())

    def test_all_adapters_reject_oversized_payloads(self) -> None:
        """Adapters must reject payloads exceeding their declared safety ceilings."""
        # HTML ceiling test
        oversized_html = b"<p>test</p>" + (b"x" * (MAX_HTML_BYTES + 10))
        with self.assertRaises(ValueError) as ctx_html:
            extract_html_markdown(oversized_html)
        self.assertIn("limit", str(ctx_html.exception).lower())

        # PDF ceiling test
        oversized_pdf = b"%PDF-" + (b"0" * (MAX_PDF_BYTES + 10))
        with self.assertRaises(ValueError) as ctx_pdf:
            extract_pdf_pages(oversized_pdf)
        self.assertIn("limit", str(ctx_pdf.exception).lower())

    def test_pptx_table_markdown_rendering(self) -> None:
        """PPTXTable must render deterministic Markdown table syntax."""
        table = PPTXTable(
            rows=[
                ["Header A", "Header B"],
                ["Row 1 Cell 1", "Row 1 Cell 2"],
                ["Row 2 Cell 1", "Row 2 Cell 2"],
            ]
        )
        md = table.to_markdown()
        self.assertIn("| Header A | Header B |", md)
        self.assertIn("| --- | --- |", md)
        self.assertIn("| Row 1 Cell 1 | Row 1 Cell 2 |", md)

    def test_pptx_table_empty_rows_returns_empty_string(self) -> None:
        """Empty PPTXTable must return empty string without errors."""
        empty_table = PPTXTable(rows=[])
        self.assertEqual(empty_table.to_markdown(), "")

        blank_cells_table = PPTXTable(rows=[["", ""]])
        self.assertEqual(blank_cells_table.to_markdown(), "")

    def test_html_strips_scripts_and_styles(self) -> None:
        """HTML extractor must strictly omit script, style, and template tags."""
        html_content = b"""
        <html>
        <head><title>Test Page</title><style>body { color: red; }</style></head>
        <body>
            <script>alert('injection');</script>
            <h1>Visible Title</h1>
            <p>Visible Paragraph with <a href="https://example.com">link</a>.</p>
            <template><span>Hidden</span></template>
        </body>
        </html>
        """
        md = extract_html_markdown(html_content)
        self.assertIn("# Visible Title", md)
        self.assertIn("Visible Paragraph with link.", md)
        self.assertNotIn("alert", md)
        self.assertNotIn("color: red", md)
        self.assertNotIn("Hidden", md)


if __name__ == "__main__":
    unittest.main()
