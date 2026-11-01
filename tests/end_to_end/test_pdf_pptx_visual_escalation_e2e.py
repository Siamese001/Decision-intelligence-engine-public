"""
tests/end_to_end/test_pdf_pptx_visual_escalation_e2e.py

End-to-End Acceptance Test Suite for Wave 3:
Selective visual paths for PDF and PowerPoint attachments.
Verifies:
  1. PDF pipeline: text-native avoidance, selective visual escalation for scanned pages,
     quality scoring, and manifest integration.
  2. PPTX pipeline: text-native avoidance, selective escalation for picture/diagram slides,
     OpenXML shape provenance, and manifest integration.
  3. Strict fail-closed visual routing invariants across both modalities.
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
import pytest
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.source_manifest import (
    SourceManifest,
    build_source_manifest,
)
from dual_pipeline.document_pipeline_core.visual_escalation import (
    validate_visual_routing_invariants,
)
from dual_pipeline.pdf_source import inspect_pdf_pages, render_pdf_page_visual
from dual_pipeline.pptx_source import extract_pptx_presentation, render_pptx_slide_visual


def _create_test_pdf(pages: List[dict]) -> bytes:
    """Build a multi-page PDF with configurable text and images."""
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
            img_obj.set_data(b"\x00\xff\x00" * 16)
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

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _create_test_pptx(slides_xml: List[str]) -> bytes:
    """Build a multi-slide PPTX package in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        content_types = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '  <Default Extension="xml" ContentType="application/xml"/>',
            '  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        ]
        for idx in range(1, len(slides_xml) + 1):
            content_types.append(
                f'  <Override PartName="/ppt/slides/slide{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            )
        content_types.append("</Types>")
        zf.writestr("[Content_Types].xml", "\n".join(content_types))

        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )

        pres_xml = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
            '  <p:sldIdLst>',
        ]
        pres_rels = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for idx in range(1, len(slides_xml) + 1):
            pres_xml.append(f'    <p:sldId id="{255 + idx}" r:id="rId{idx}"/>')
            pres_rels.append(
                f'  <Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{idx}.xml"/>'
            )
        pres_xml.append("  </p:sldIdLst>\n</p:presentation>")
        pres_rels.append("</Relationships>")

        zf.writestr("ppt/presentation.xml", "\n".join(pres_xml))
        zf.writestr("ppt/_rels/presentation.xml.rels", "\n".join(pres_rels))

        for idx, s_xml in enumerate(slides_xml, start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", s_xml)

    return buf.getvalue()


class TestPdfPptxVisualEscalationE2E:
    def test_e2e_text_native_pdf_manifest_and_loader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_pdf_test_") as tmpdir:
            pdf_bytes = _create_test_pdf([
                {"text": "Board of Directors Q4 Strategy Briefing. Enterprise growth objectives."},
                {"text": "Key governance milestones and compliance deliverables for FY2026."},
            ])
            pdf_path = Path(tmpdir) / "board_briefing.pdf"
            pdf_path.write_bytes(pdf_bytes)

            meta: dict = {}
            content, f_hash, fmt = load_document_content(pdf_path, metadata=meta)
            assert fmt == "pdf"
            assert len(f_hash) == 64
            assert "Board of Directors Q4 Strategy" in content
            assert "Key governance milestones" in content
            assert meta["_source_coverage"]["pages_count"] == 2
            assert meta["_source_coverage"]["escalated_pages"] == []

            manifest = build_source_manifest([pdf_path])
            assert len(manifest.sources) == 1
            entry = manifest.sources[0]
            assert entry.format == "pdf"
            assert entry.pages is not None
            assert len(entry.pages) == 2

            for p in entry.pages:
                assert p.visual_disposition == "TEXT_ONLY"
                assert p.image_count == 0
                assert p.quality_score >= 0.5

            assert entry.unit_coverage is not None
            assert entry.unit_coverage.visual_policy == "ON_LOW_TEXT_OR_SCANNED"
            assert entry.unit_coverage.details.get("escalated_pages") == 0

    def test_e2e_mixed_pdf_manifest_and_escalation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_pdf_mixed_") as tmpdir:
            pdf_bytes = _create_test_pdf([
                {"text": "Page 1: Contract terms and legal scope definition."},
                {"text": "", "has_image": True},  # Page 2: Scanned exhibit
                {"text": "Page 3: Signatures, dates, and closing executive addenda."},
            ])
            pdf_path = Path(tmpdir) / "executed_contract.pdf"
            pdf_path.write_bytes(pdf_bytes)

            manifest = build_source_manifest([pdf_path])
            entry = manifest.sources[0]
            assert entry.pages is not None
            assert len(entry.pages) == 3

            assert entry.pages[0].visual_disposition == "TEXT_ONLY"
            assert entry.pages[1].visual_disposition == "ESCALATE_VISUAL"
            assert entry.pages[1].flagged_regions == ["p2#img1"]
            assert entry.pages[2].visual_disposition == "TEXT_ONLY"
            assert entry.unit_coverage.details.get("escalated_pages") == 1

            # Verify selective visual routing:
            report = inspect_pdf_pages(pdf_bytes, title="Contract")
            with pytest.raises(ValueError, match="is text-native"):
                render_pdf_page_visual(1, report)
            v_action = render_pdf_page_visual(2, report)
            assert v_action["locator"] == "p2"
            assert v_action["visual_action"] == "RENDER_AND_OCR"

    def test_e2e_text_native_pptx_manifest_and_loader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_pptx_test_") as tmpdir:
            slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Enterprise Architecture</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Body"/><p:nvPr/></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Unidirectional dependencies enforce system stability across all layers.</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
            slide2_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Governance Principles</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
            pptx_bytes = _create_test_pptx([slide1_xml, slide2_xml])
            pptx_path = Path(tmpdir) / "architecture_deck.pptx"
            pptx_path.write_bytes(pptx_bytes)

            meta: dict = {}
            content, f_hash, fmt = load_document_content(pptx_path, metadata=meta)
            assert fmt == "pptx"
            assert len(f_hash) == 64
            assert "Enterprise Architecture" in content
            assert "Governance Principles" in content

            manifest = build_source_manifest([pptx_path])
            entry = manifest.sources[0]
            assert entry.format == "pptx"
            assert entry.slides is not None
            assert len(entry.slides) == 2

            for s in entry.slides:
                assert s.visual_disposition == "TEXT_ONLY"

            assert entry.unit_coverage.visual_policy == "ON_NON_TEXT_SHAPES"
            assert entry.unit_coverage.details.get("escalated_slides") == 0

    def test_e2e_mixed_pptx_manifest_and_escalation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e2e_pptx_mixed_") as tmpdir:
            slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Agenda</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
            slide2_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:graphicFrame>
        <p:nvGraphicFramePr><p:cNvPr id="99" name="Process Map"/><p:nvPr/></p:nvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">
            <dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>"""
            pptx_bytes = _create_test_pptx([slide1_xml, slide2_xml])
            pptx_path = Path(tmpdir) / "roadmap_deck.pptx"
            pptx_path.write_bytes(pptx_bytes)

            manifest = build_source_manifest([pptx_path])
            entry = manifest.sources[0]
            assert entry.slides is not None
            assert len(entry.slides) == 2
            assert entry.slides[0].visual_disposition == "TEXT_ONLY"
            assert entry.slides[1].visual_disposition == "HITL_REQUIRED"
            assert entry.unit_coverage.details.get("escalated_slides") == 1

            pres = extract_pptx_presentation(pptx_bytes)
            with pytest.raises(ValueError, match="is text-native"):
                render_pptx_slide_visual(1, pres)
            action = render_pptx_slide_visual(2, pres)
            assert action["locator"] == "slide2"
            assert action["disposition"] == "HITL_REQUIRED"

    def test_e2e_cross_format_routing_invariants(self) -> None:
        pdf_bytes = _create_test_pdf([
            {"text": "Valid text page 1."},
            {"text": "", "has_image": True},
        ])
        pptx_slide_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Pure Text Slide</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        pptx_bytes = _create_test_pptx([pptx_slide_xml])

        pdf_rep = inspect_pdf_pages(pdf_bytes, title="Doc")
        pptx_pres = extract_pptx_presentation(pptx_bytes)

        # 1. Routing pure-text page fails closed
        with pytest.raises(ValueError, match="pure-text PDF page 'p1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(pages=pdf_rep.pages, attempted_raster_targets=["p1"])

        # 2. Routing pure-text slide fails closed
        with pytest.raises(ValueError, match="pure-text PPTX slide 'slide1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(slides=pptx_pres.slides, attempted_raster_targets=["slide1"])

        # 3. Valid escalated target passes
        validate_visual_routing_invariants(pages=pdf_rep.pages, attempted_raster_targets=["p2"])
