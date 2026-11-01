"""
tests/test_pdf_attachment_projection.py

Unit and projection tests for PDF attachments:
- Verifies text-native pages avoid raster/vision processing.
- Verifies scanned or image-heavy pages escalate to visual processing.
- Verifies mixed PDFs only escalate visual pages.
- Verifies encrypted PDFs route to HITL_REQUIRED.
- Verifies text quality scores, locators, and source-unit projection.
"""
from __future__ import annotations

import io
import pytest
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from dual_pipeline.document_pipeline_core.format_source_units import build_pdf_source_units
from dual_pipeline.document_pipeline_core.visual_escalation import (
    VisualDisposition,
    compute_text_quality,
    validate_visual_routing_invariants,
)
from dual_pipeline.pdf_source import (
    PDFExtractionReport,
    extract_pdf_pages,
    inspect_pdf_pages,
    normalize_pdf_text,
    parse_page_range,
    render_pdf_markdown,
    render_pdf_page_visual,
    render_pdf_range,
)


def _build_test_pdf(pages: list[dict], password: str | None = None) -> bytes:
    """Build a valid multi-page PDF with configurable text, images, and encryption."""
    writer = pypdf.PdfWriter()

    for p_spec in pages:
        page = writer.add_blank_page(width=612, height=792)
        text = p_spec.get("text", "")
        has_image = p_spec.get("has_image", False)

        resources = DictionaryObject()

        if text:
            font_obj = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            font_ref = writer._add_object(font_obj)
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})

            t_stream = DecodedStreamObject()
            t_stream.set_data(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin1"))
            page[NameObject("/Contents")] = writer._add_object(t_stream)

        if has_image:
            img_obj = DecodedStreamObject()
            img_obj.set_data(b"\xff\x00\x00" * 16)
            img_obj.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(4),
                NameObject("/Height"): NumberObject(4),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            })
            img_ref = writer._add_object(img_obj)
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): img_ref})

        page[NameObject("/Resources")] = resources

    if password:
        writer.encrypt(password)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestPdfAttachmentProjection:
    def test_text_native_pdf_avoids_visual_processing(self) -> None:
        raw_pdf = _build_test_pdf([
            {"text": "Executive strategy document with rich native text content and verified headings."},
            {"text": "Section 2 provides operational guidelines and detailed staffing requirements."},
        ])
        report = inspect_pdf_pages(raw_pdf, title="Strategy Document")

        assert report.total_pages == 2
        assert report.text_native_pages == [1, 2]
        assert report.escalated_pages == []
        assert report.hitl_pages == []
        assert not report.is_encrypted

        for p in report.pages:
            assert p.disposition == "TEXT_ONLY"
            assert p.reason == "TEXT_NATIVE_CONFIRMED"
            assert p.image_count == 0
            assert p.quality_score >= 0.5

        # Fail closed: pure-text page must never be sent to visual processing
        with pytest.raises(ValueError, match="is text-native; routing to visual processing is prohibited"):
            render_pdf_page_visual(1, report)

        with pytest.raises(ValueError, match="pure-text PDF page 'p1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(
                pages=report.pages,
                attempted_raster_targets=["p1"],
            )

    def test_scanned_pdf_page_escalates_to_visual(self) -> None:
        raw_pdf = _build_test_pdf([
            {"text": "", "has_image": True},
        ])
        report = inspect_pdf_pages(raw_pdf, title="Scanned Invoices")

        assert report.total_pages == 1
        assert report.text_native_pages == []
        assert report.escalated_pages == [1]
        assert report.pages[0].disposition == "ESCALATE_VISUAL"
        assert report.pages[0].reason == "SCANNED_OR_LOW_TEXT_PAGE"
        assert report.pages[0].flagged_regions == ["p1#img1"]

        # Visual routing succeeds for escalated page
        action = render_pdf_page_visual(1, report)
        assert action["locator"] == "p1"
        assert action["disposition"] == "ESCALATE_VISUAL"
        assert action["visual_action"] == "RENDER_AND_OCR"

        # Fail closed: direct text-only extraction must not return an empty scanned page silently
        with pytest.raises(ValueError, match="is a scanned image with no extractable text; selective visual escalation required"):
            extract_pdf_pages(raw_pdf)

    def test_mixed_pdf_only_escalates_visual_pages(self) -> None:
        raw_pdf = _build_test_pdf([
            {"text": "Page 1 contains valid financial ledger statements and revenue counts."},
            {"text": "", "has_image": True},
            {"text": "Page 3 contains closing executive notes and audit confirmations."},
        ])
        report = inspect_pdf_pages(raw_pdf, title="Mixed Ledger")

        assert report.total_pages == 3
        assert report.text_native_pages == [1, 3]
        assert report.escalated_pages == [2]

        assert report.pages[0].disposition == "TEXT_ONLY"
        assert report.pages[1].disposition == "ESCALATE_VISUAL"
        assert report.pages[2].disposition == "TEXT_ONLY"

        # Page 1 fails closed if routed to vision
        with pytest.raises(ValueError, match="is text-native"):
            render_pdf_page_visual(1, report)

        # Page 2 successfully routes to visual inspection
        visual_req = render_pdf_page_visual(2, report)
        assert visual_req["locator"] == "p2"
        assert visual_req["flagged_regions"] == ["p2#img1"]

        # Page 3 fails closed if routed to vision
        with pytest.raises(ValueError, match="is text-native"):
            render_pdf_page_visual(3, report)

    def test_encrypted_pdf_becomes_hitl_required(self) -> None:
        raw_pdf = _build_test_pdf(
            [{"text": "Confidential Board Meeting Minutes"}],
            password="locked_credentials",
        )
        report = inspect_pdf_pages(raw_pdf, title="Confidential Board", fail_on_encrypted=False)

        assert report.is_encrypted
        assert report.encryption_status == "LOCKED"
        assert report.hitl_pages == [1]
        assert report.pages[0].disposition == "HITL_REQUIRED"
        assert report.pages[0].reason == "ENCRYPTED_PDF_CREDENTIALS_REQUIRED"
        assert report.pages[0].locator == "p1"

        # Direct extraction with fail_on_encrypted=True raises ValueError
        with pytest.raises(ValueError, match="encrypted PDF cannot be extracted without credentials"):
            inspect_pdf_pages(raw_pdf, fail_on_encrypted=True)

    def test_pdf_text_quality_scores(self) -> None:
        # High quality text
        q_high = compute_text_quality("The quarterly recurring revenue grew by twenty-five percent in North America.", 0)
        assert q_high.quality_score >= 0.65
        assert not q_high.is_scanned
        assert not q_high.is_low_text

        # Scanned low text with image
        q_scanned = compute_text_quality("inv", 1)
        assert q_scanned.is_scanned
        assert q_scanned.quality_score < 0.3

        # Empty page
        q_empty = compute_text_quality("", 0)
        assert q_empty.char_count == 0
        assert q_empty.quality_score == 0.0

    def test_pdf_source_units_generation(self) -> None:
        raw_pdf = _build_test_pdf([
            {"text": "Audit summary page 1."},
            {"text": "Audit details page 2."},
        ])
        report = inspect_pdf_pages(raw_pdf, title="Audit")
        units = build_pdf_source_units(report, "abcdef1234567890")

        assert len(units) == 2
        assert units[0].locator == "p1"
        assert units[0].unit_type == "page"
        assert units[0].format == "pdf"
        assert "Audit summary page 1" in units[0].content
        assert units[0].metadata["disposition"] == "TEXT_ONLY"
        assert units[1].locator == "p2"

    def test_render_pdf_range_and_markdown(self) -> None:
        pages = ["Page one text.", "Page two text.", "Page three text."]
        md = render_pdf_markdown(pages, title="Test Doc")
        assert "# Test Doc" in md
        assert "## Page 1\n\nPage one text." in md

        ranged = render_pdf_range(pages, "2-3")
        assert "## Page 2" in ranged
        assert "## Page 3" in ranged
        assert "## Page 1" not in ranged

        start, end = parse_page_range("1-2", page_count=3)
        assert (start, end) == (1, 2)
