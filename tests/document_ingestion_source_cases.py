"""Mixin for the parent's Wave1AdversarialTests; no standalone test execution."""
from __future__ import annotations

import hashlib
import io
import zipfile

from dual_pipeline.document_pipeline_core.docx_coverage import extract_docx_with_coverage
from dual_pipeline.evidence_field_limits import validate_docx_cell_locator


class SourceAdversarialCases:
    @staticmethod
    def _docx_body(body: str) -> bytes:
        stream = io.BytesIO()
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body>' + body + '</w:body></w:document>')
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", xml)
        return stream.getvalue()

    def test_nested_textbox_words_remain_separate(self):
        source = self._docx_body(
            '<w:p><w:r><w:t>Before</w:t></w:r><w:txbxContent>'
            '<w:p><w:r><w:t>Inside</w:t></w:r></w:p></w:txbxContent>'
            '<w:r><w:t>After</w:t></w:r></w:p>')
        text, coverage = extract_docx_with_coverage(source)
        self.assertIn("Before Inside After", text)
        self.assertNotIn("BeforeInsideAfter", text)
        self.assertEqual(coverage["preserved_text_nodes"], 3)
        self.assertEqual(coverage["expected_text_nodes"], 3)

    def test_adjacent_runs_are_not_split_and_repeated_text_is_not_deduplicated(self):
        source = self._docx_body(
            '<w:p><w:r><w:t>Pro</w:t></w:r><w:r><w:t>ject</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Project</w:t></w:r></w:p>')
        text, coverage = extract_docx_with_coverage(source)
        self.assertEqual(text.count("Project"), 2)
        self.assertNotIn("Pro ject", text)
        self.assertEqual(coverage["preserved_text_nodes"], 3)

    def test_cell_quote_cannot_point_at_a_different_cell(self):
        source = "[T1.R1.C1] approved [ /not-a-marker ] [/T1.R1.C1] | [T1.R1.C2] rejected [/T1.R1.C2]"
        locator = {"kind": "cell", "value": "T1.R1.C1"}
        with self.assertRaisesRegex(ValueError, "quoted evidence"):
            validate_docx_cell_locator({"text": "rejected"}, locator, source)
        validate_docx_cell_locator({"text": "approved"}, locator, source)

    def test_cell_digest_cannot_be_reused_with_another_quote(self):
        source = "[T1.R1.C1] approved [/T1.R1.C1]"
        locator = {"kind": "cell", "value": "T1.R1.C1", "quote_digest": "0" * 64}
        with self.assertRaisesRegex(ValueError, "quoted evidence"):
            validate_docx_cell_locator({"text": "approved"}, locator, source)

    def test_nested_table_cells_have_independent_boundaries(self):
        source = self._docx_body(
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Outer</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Inner</w:t></w:r></w:p>'
            '</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>')
        text, coverage = extract_docx_with_coverage(source)
        self.assertEqual(coverage["cell_count"], 2)
        self.assertEqual(coverage["preserved_text_nodes"], 2)
        validate_docx_cell_locator({"text": "Inner"}, {"kind": "cell", "value": "T2.R1.C1"}, text)
        with self.assertRaisesRegex(ValueError, "quoted evidence"):
            validate_docx_cell_locator({"text": "Outer"}, {"kind": "cell", "value": "T2.R1.C1"}, text)

    def test_action_paraphrase_validates_supplied_cell_digest(self):
        source = "[T1.R1.C1] Client requested a review. [/T1.R1.C1]"
        item = {"action": "Review the client request."}
        locator = {"kind": "cell", "value": "T1.R1.C1"}
        validate_docx_cell_locator(item, locator, source)
        locator["quote_digest"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_docx_cell_locator(item, locator, source)
        locator["quote_digest"] = hashlib.sha256(b"Client requested a review.").hexdigest()
        validate_docx_cell_locator(item, locator, source)

    def test_duplicate_cell_markers_are_rejected_as_ambiguous(self):
        source = "[T1.R1.C1] safe [T1.R1.C1] injected [/T1.R1.C1]"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            validate_docx_cell_locator({"text": "safe"}, {"kind": "cell", "value": "T1.R1.C1"}, source)

    def test_unconsumed_visible_text_fails_coverage(self):
        source = self._docx_body('<w:sdt><w:sdtContent><w:t>orphaned</w:t></w:sdtContent></w:sdt>')
        with self.assertRaisesRegex(ValueError, "unaccounted"):
            extract_docx_with_coverage(source)

    def test_embedded_external_text_cannot_silently_pass_coverage(self):
        source = self._docx_body('<w:p><w:r><w:t>Body</w:t></w:r></w:p><w:altChunk/>')
        with self.assertRaisesRegex(ValueError, "compatible extractor"):
            extract_docx_with_coverage(source)
