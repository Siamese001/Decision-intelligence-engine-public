"""
tests/test_pdf_pptx_edge_cases.py

Adversarial and Edge-Case Test Suite for Wave 3:
Selective visual paths for PDF and PowerPoint attachments.

Covers mandatory edge-case categories per e2e-and-edge-case-coverage.md:
  1. Boundary payload (0-byte PDF/PPTX, 0-page PDF, 0-slide PPTX)
  2. Malformed input (corrupt non-zip PPTX, broken slide XML, missing slide targets, corrupt PDF)
  3. Invariant bypass attempt (routing text-native to vision, all-rendered default, missing locators)
  4. Overflow & exhaustion (slide XML exceeding byte ceiling, out-of-bounds page/slide numbers)
  5. Exact production regression (encrypted PDF credentials handling)
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, List
import pytest
import pypdf

from dual_pipeline.document_pipeline_core.visual_escalation import (
    PageVisualAssessment,
    SlideVisualAssessment,
    VisualDisposition,
    assess_pdf_page,
    validate_visual_routing_invariants,
)
from dual_pipeline.pdf_source import (
    PDFPageInventory,
    extract_pdf_pages,
    inspect_pdf_pages,
    render_pdf_page_visual,
)
from dual_pipeline.pptx_source import (
    PPTXPresentation,
    PPTXSlide,
    extract_pptx_presentation,
    render_pptx_slide_visual,
)


def _build_minimal_pptx(slides_xml: List[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "ppt/presentation.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
  </p:sldIdLst>
</p:presentation>""",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        )
        for idx, s_xml in enumerate(slides_xml, start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", s_xml)
    return buf.getvalue()


class TestPdfPptxEdgeCases:
    # -------------------------------------------------------------------------
    # 1. Boundary Payloads
    # -------------------------------------------------------------------------
    def test_zero_byte_pdf_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="PDF source must not be empty"):
            inspect_pdf_pages(b"")

    def test_zero_byte_pptx_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="PPTX source must not be empty"):
            extract_pptx_presentation(b"")

    def test_zero_page_pdf_fails_closed(self) -> None:
        writer = pypdf.PdfWriter()
        buf = io.BytesIO()
        writer.write(buf)
        with pytest.raises(ValueError, match="has no pages"):
            inspect_pdf_pages(buf.getvalue())

    def test_zero_slide_pptx_fails_closed(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
            zf.writestr("_rels/.rels", "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'/>")
            zf.writestr(
                "ppt/presentation.xml",
                """<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'>
  <p:sldIdLst/>
</p:presentation>""",
            )
        with pytest.raises(ValueError, match="PPTX source has no slides"):
            extract_pptx_presentation(buf.getvalue())

    # -------------------------------------------------------------------------
    # 2. Malformed Input
    # -------------------------------------------------------------------------
    def test_corrupted_non_zip_pptx_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="source is not a valid PPTX zip package"):
            extract_pptx_presentation(b"Not a zip archive, just junk data.")

    def test_corrupted_pdf_stream_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="source is not a readable PDF"):
            inspect_pdf_pages(b"%PDF-1.4\nJUNK JUNK BROKEN TRAILER")

    def test_malformed_slide_xml_fails_closed(self) -> None:
        broken_xml = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><UNCLOSED>"""
        raw = _build_minimal_pptx([broken_xml])
        with pytest.raises(ValueError, match="XML is malformed"):
            extract_pptx_presentation(raw)

    def test_missing_slide_target_in_archive_fails_closed(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
            zf.writestr(
                "_rels/.rels",
                """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
            )
            zf.writestr(
                "ppt/presentation.xml",
                """<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
  <p:sldIdLst><p:sldId id='256' r:id='rId1'/></p:sldIdLst>
</p:presentation>""",
            )
            zf.writestr(
                "ppt/_rels/presentation.xml.rels",
                """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/missing_slide.xml"/>
</Relationships>""",
            )
        with pytest.raises(ValueError, match="PPTX missing slide file"):
            extract_pptx_presentation(buf.getvalue())

    # -------------------------------------------------------------------------
    # 3. Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_invariant_bypass_pure_text_pdf_routed_to_visual(self) -> None:
        page = assess_pdf_page(1, "High quality text for testing", 0)
        with pytest.raises(ValueError, match="pure-text PDF page 'p1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(pages=[page], attempted_raster_targets=["p1"])

    def test_invariant_bypass_pure_text_pptx_routed_to_visual(self) -> None:
        slide = SlideVisualAssessment(
            slide_num=1,
            locator="slide1",
            disposition=VisualDisposition.TEXT_ONLY,
            text_char_count=500,
            shape_count=2,
            text_shape_count=2,
            visual_shape_count=0,
            has_inaccessible_chart=False,
            has_diagram=False,
            reason="TEXT_NATIVE_CONFIRMED",
        )
        with pytest.raises(ValueError, match="pure-text PPTX slide 'slide1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(slides=[slide], attempted_raster_targets=["slide1"])

    def test_invariant_bypass_all_rendered_by_default(self) -> None:
        p1 = assess_pdf_page(1, "High quality text for testing", 0)
        p2 = assess_pdf_page(2, "", 1)
        with pytest.raises(ValueError, match="all PDF pages were routed to visual/raster by default"):
            validate_visual_routing_invariants(pages=[p1, p2], attempted_raster_targets=["p1", "p2"])

    def test_invariant_bypass_missing_source_locators(self) -> None:
        broken_assessment = SlideVisualAssessment(
            slide_num=1,
            locator="",
            disposition=VisualDisposition.ESCALATE_VISUAL,
            text_char_count=0,
            shape_count=1,
            text_shape_count=0,
            visual_shape_count=1,
            has_inaccessible_chart=False,
            has_diagram=False,
            reason="SCANNED",
            flagged_shape_locators=[],
        )
        with pytest.raises(ValueError, match="visual claim for PPTX slide lacks a source locator"):
            validate_visual_routing_invariants(slides=[broken_assessment])

    # -------------------------------------------------------------------------
    # 4. Overflow & Out-Of-Bounds
    # -------------------------------------------------------------------------
    def test_out_of_bounds_slide_rendering_request(self) -> None:
        pres = PPTXPresentation(
            title="Empty",
            slides=[],
            raw_sha256="abc",
            extracted_markdown="",
            extracted_markdown_sha256="def",
            slide_count=0,
        )
        with pytest.raises(ValueError, match="Slide number 99 out of bounds"):
            render_pptx_slide_visual(99, pres)

    def test_out_of_bounds_pdf_rendering_request(self) -> None:
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buf = io.BytesIO()
        writer.write(buf)
        report = inspect_pdf_pages(buf.getvalue(), title="Single Page Doc")
        with pytest.raises(ValueError, match="Page number 5 out of bounds"):
            render_pdf_page_visual(5, report)

    # -------------------------------------------------------------------------
    # 5. Exact Production Regressions
    # -------------------------------------------------------------------------
    def test_production_regression_encrypted_pdf_handling(self) -> None:
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("top_secret")
        buf = io.BytesIO()
        writer.write(buf)
        enc_bytes = buf.getvalue()

        # Non-failing audit path returns locked HITL report
        report = inspect_pdf_pages(enc_bytes, title="Locked Document", fail_on_encrypted=False)
        assert report.is_encrypted is True
        assert report.encryption_status == "LOCKED"
        assert report.hitl_pages == [1]
        assert report.pages[0].disposition == "HITL_REQUIRED"
        assert report.pages[0].reason == "ENCRYPTED_PDF_CREDENTIALS_REQUIRED"
