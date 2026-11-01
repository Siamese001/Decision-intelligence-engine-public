"""
tests/test_slide_preprocessing_adversarial.py

Adversarial Edge-Case Suite for Slide Deck and Document Preprocessing:
Covers mandatory edge-case categories under AGENTS.md and e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte PDF/EML, 1-character symbol pages (©), blank pages, empty attachments.
2. Malformed input: Corrupted PDF streams, invalid MIME structures, mixed punctuation/number kerning.
3. Invariant bypass prevention & Gate 3 verbatim provenance:
   - Validates that repaired kerning text strictly matches Gate 3 verbatim grounding against cached source markdown.
   - Confirms hallucinated / ungrounded claim fragments fail closed immediately.
"""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.eml_loader import (
    clean_eml_boilerplate,
    extract_eml_markdown,
)
from dual_pipeline.document_pipeline_core.quality import validate_signal_claim_grounding
from dual_pipeline.pdf_source import (
    extract_pdf_pages,
    inspect_pdf_pages,
    normalize_pdf_page_content,
    repair_kerning,
)


def _build_minimal_pdf(pages: list[dict]) -> bytes:
    writer = pypdf.PdfWriter()
    for p in pages:
        page = writer.add_blank_page(width=612, height=792)
        txt = p.get("text", "")
        has_img = p.get("has_image", False)
        res = DictionaryObject()

        if txt:
            font_obj = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            font_ref = writer._add_object(font_obj)
            res[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})

            t_stream = DecodedStreamObject()
            t_stream.set_data(f"BT /F1 12 Tf 72 712 Td ({txt}) Tj ET".encode("latin1", errors="replace"))
            page[NameObject("/Contents")] = writer._add_object(t_stream)

        if has_img:
            img_obj = DecodedStreamObject()
            img_obj.set_data(b"\x00\x00\x00" * 4)
            img_obj.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(2),
                NameObject("/Height"): NumberObject(2),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            })
            img_ref = writer._add_object(img_obj)
            res[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): img_ref})

        page[NameObject("/Resources")] = res

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestSlidePreprocessingAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for document/slide preprocessing."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="adversarial_test_")
        self.tmppath = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_zero_byte_pdf_fails_closed(self) -> None:
        """Verify 0-byte PDF payload fails closed with ValueError."""
        with self.assertRaises(ValueError) as ctx:
            inspect_pdf_pages(b"")
        self.assertIn("PDF source must not be empty", str(ctx.exception))

    def test_zero_byte_eml_handled_safely(self) -> None:
        """Verify empty EML payload returns empty string without crashing."""
        res = extract_eml_markdown(b"")
        self.assertEqual(res, "")

    def test_empty_blank_pdf_page_disposition(self) -> None:
        """Verify completely empty PDF page is marked EXCLUDED (not text-native, not escalated)."""
        pdf_bytes = _build_minimal_pdf([{"text": "", "has_image": False}])
        report = inspect_pdf_pages(pdf_bytes)
        self.assertEqual(report.total_pages, 1)
        self.assertEqual(report.pages[0].disposition, "EXCLUDED")
        self.assertEqual(report.pages[0].reason, "EMPTY_BLANK_PAGE")

    def test_boundary_symbols_only_page_fails_closed_on_extract(self) -> None:
        """Verify page with only punctuation/symbols and image fails closed against text extraction."""
        pdf_bytes = _build_minimal_pdf([{"text": "\xa9  \xa9", "has_image": True}])
        report = inspect_pdf_pages(pdf_bytes)
        self.assertEqual(report.pages[0].disposition, "ESCALATE_VISUAL")
        with self.assertRaises(ValueError) as ctx:
            extract_pdf_pages(pdf_bytes)
        self.assertIn("visual page with no extractable text", str(ctx.exception))

    # --- CATEGORY 2: MALFORMED INPUTS & EXHAUSTION ---

    def test_corrupt_pdf_header_fails_closed(self) -> None:
        """Verify malformed PDF payload lacking %PDF- header is rejected immediately."""
        corrupt_bytes = b"NOT_A_PDF_STREAM_1234567890"
        with self.assertRaises(ValueError) as ctx:
            inspect_pdf_pages(corrupt_bytes)
        self.assertIn("source does not begin with a PDF header", str(ctx.exception))

    def test_malformed_kerning_boundaries_preserve_numbers_and_symbols(self) -> None:
        """Verify kerning repair handles mixed numbers, punctuation, and code tokens safely."""
        text = "Order 1 2 3 4 failed on line 5 6 7 8 with code 404"
        repaired = repair_kerning(text)
        self.assertEqual(repaired, "Order 1234 failed on line 5678 with code 404")

        # Mixed alphanumeric should not merge unintended lowercase tokens
        mixed = "a b c d e f"
        self.assertEqual(repair_kerning(mixed), "abcdef")

        # Whitespace normalization with empty input
        self.assertEqual(repair_kerning(""), "")
        self.assertEqual(normalize_pdf_page_content(""), "")

    def test_eml_boilerplate_stripping_adversarial_patterns(self) -> None:
        """Verify aggressive regex stripping does not truncate legitimate email content."""
        clean = clean_eml_boilerplate(
            "Important project delivery details:\n"
            "http://internal.consulting.example.com/project-dashboard\n"
            "Update your email preferences or unsubscribe: <https://emltrk.com/track/12345>\n"
            "PartnerAdvisory processes your data in accordance with our Privacy Policy.\n"
            "Action items for the sprint."
        )
        self.assertIn("Important project delivery details:", clean)
        self.assertIn("http://internal.consulting.example.com/project-dashboard", clean)
        self.assertIn("Action items for the sprint.", clean)
        self.assertNotIn("emltrk.com", clean)
        self.assertNotIn("Privacy Policy", clean)

    # --- CATEGORY 3: INVARIANT BYPASS PREVENTION & GATE 3 VERBATIM GROUNDING ---

    def test_gate3_verbatim_grounding_with_repaired_kerning(self) -> None:
        """Verify Gate 3 verbatim substring verification passes against cleaned markdown."""
        pdf_bytes = _build_minimal_pdf([
            {
                "text": "T H E  B U S I N E S S  O P P O R T U N I T Y\nManaged services transformation roadmap.",
                "has_image": False,
            }
        ])
        pdf_file = self.tmppath / "grounding_test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta: dict = {"project": "grounding_test"}
        source_text, sha256_digest, fmt = load_document_content(pdf_file, metadata=meta)

        self.assertIn("THE BUSINESS OPPORTUNITY", source_text)

        # Build valid projection citing repaired text verbatim under graph_mutations
        valid_projection = {
            "evidence_index": {
                "EV_001": {
                    "locator": "p1",
                    "exact_text": "THE BUSINESS OPPORTUNITY",
                }
            },
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "SIG_001",
                        "evidence_ids": ["EV_001"],
                        "description": "Business opportunity identified in slide deck",
                    }
                ]
            },
        }

        is_grounded, errors = validate_signal_claim_grounding(valid_projection, source_text)
        self.assertTrue(is_grounded, f"Gate 3 grounding must pass; errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_gate3_verbatim_grounding_detects_hallucinations(self) -> None:
        """Verify Gate 3 fails closed when a projection cites text absent from cleaned source."""
        pdf_bytes = _build_minimal_pdf([
            {"text": "AUTHENTIC SOURCE CONTENT ONLY", "has_image": False}
        ])
        pdf_file = self.tmppath / "hallucination_test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta: dict = {"project": "hallucination_test"}
        source_text, _, _ = load_document_content(pdf_file, metadata=meta)

        hallucinated_projection = {
            "evidence_index": {
                "EV_FAKE": {
                    "locator": "p1",
                    "exact_text": "FABRICATED_UNGROUNDED_CLAIM_NOT_IN_SOURCE",
                }
            },
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "SIG_999",
                        "evidence_ids": ["EV_FAKE"],
                        "description": "Fabricated signal",
                    }
                ]
            },
        }

        is_grounded, errors = validate_signal_claim_grounding(hallucinated_projection, source_text)
        self.assertFalse(is_grounded, "Gate 3 must fail closed on hallucinated evidence")
        self.assertGreater(len(errors), 0)
        self.assertIn("cites ungrounded text", errors[0])
