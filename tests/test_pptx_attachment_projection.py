"""
tests/test_pptx_attachment_projection.py

Unit and projection tests for PowerPoint attachments:
- Verifies text-native slides avoid visual/raster processing.
- Verifies diagram-only slides route to HITL_REQUIRED.
- Verifies mixed slides escalate to ESCALATE_VISUAL with shape locators.
- Verifies cached charts extract as TEXT_ONLY and inaccessible charts escalate to HITL_REQUIRED.
- Verifies geometric reading order sorting and hyperlink extraction.
- Verifies source-unit projection and fail-closed routing invariants.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List, Optional
import pytest

from dual_pipeline.document_pipeline_core.format_source_units import build_pptx_source_units
from dual_pipeline.document_pipeline_core.visual_escalation import (
    VisualDisposition,
    validate_visual_routing_invariants,
)
from dual_pipeline.pptx_source import (
    PPTXPresentation,
    extract_pptx_markdown,
    extract_pptx_presentation,
    render_pptx_slide_visual,
)


def _build_test_pptx(
    slides_xml: List[str],
    slide_rels: Optional[Dict[int, List[Dict[str, str]]]] = None,
    extra_parts: Optional[Dict[str, bytes]] = None,
) -> bytes:
    """Helper to assemble a valid OpenXML PPTX in memory."""
    buf = io.BytesIO()
    slide_rels = slide_rels or {}
    extra_parts = extra_parts or {}

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
        for part_name in extra_parts:
            if "chart" in part_name:
                content_types.append(
                    f'  <Override PartName="/{part_name.lstrip("/")}" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
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
            if idx in slide_rels:
                s_rels_xml = [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                ]
                for r in slide_rels[idx]:
                    s_rels_xml.append(
                        f'  <Relationship Id="{r["id"]}" Type="{r["type"]}" Target="{r["target"]}" TargetMode="{r.get("target_mode", "Internal")}"/>'
                    )
                s_rels_xml.append("</Relationships>")
                zf.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", "\n".join(s_rels_xml))

        for part_name, content in extra_parts.items():
            zf.writestr(part_name.lstrip("/"), content)

    return buf.getvalue()


class TestPptxAttachmentProjection:
    def test_text_native_slide_avoids_visual_processing(self) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Quarterly Operations Review</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Body"/><p:nvPr/></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>All enterprise service level agreements were met in Q3.</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        raw = _build_test_pptx([slide1_xml])
        pres = extract_pptx_presentation(raw)

        assert pres.slide_count == 1
        assert pres.text_native_slides == [1]
        assert pres.escalated_slides == []
        assert pres.hitl_slides == []
        assert pres.slides[0].disposition == "TEXT_ONLY"
        assert pres.slides[0].disposition_reason == "TEXT_NATIVE_CONFIRMED"

        # Fail closed: routing pure-text slide to visual processing is prohibited
        with pytest.raises(ValueError, match="is text-native; routing to visual processing is prohibited"):
            render_pptx_slide_visual(1, pres)

        with pytest.raises(ValueError, match="pure-text PPTX slide 'slide1' was routed to visual/raster processing"):
            validate_visual_routing_invariants(
                slides=pres.slides,
                attempted_raster_targets=["slide1"],
            )

    def test_diagram_only_slide_becomes_hitl_required(self) -> None:
        # Slide with graphic frame/diagram but zero text body
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:graphicFrame>
        <p:nvGraphicFramePr><p:cNvPr id="10" name="Architecture Diagram"/><p:nvPr/></p:nvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">
            <dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        raw = _build_test_pptx([slide1_xml])
        pres = extract_pptx_presentation(raw)

        assert pres.slide_count == 1
        assert pres.hitl_slides == [1]
        assert pres.slides[0].disposition == "HITL_REQUIRED"
        assert pres.slides[0].disposition_reason == "DIAGRAM_ONLY_SLIDE_NO_EXTRACTABLE_TEXT"
        assert "slide1#shape10" in pres.slides[0].flagged_shape_locators

        # Visual routing succeeds for HITL slide
        action = render_pptx_slide_visual(1, pres)
        assert action["locator"] == "slide1"
        assert action["disposition"] == "HITL_REQUIRED"
        assert action["visual_action"] == "RENDER_AND_INSPECT_SHAPES"

    def test_mixed_slide_with_images_escalates_visual(self) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Product Roadmap</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:pic>
        <p:nvPicPr><p:cNvPr id="20" name="Mockup Screenshot"/><p:nvPr/></p:nvPicPr>
        <p:blipFill><a:blip/></p:blipFill>
        <p:spPr/>
      </p:pic>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        raw = _build_test_pptx([slide1_xml])
        pres = extract_pptx_presentation(raw)

        assert pres.slide_count == 1
        assert pres.escalated_slides == [1]
        assert pres.slides[0].disposition == "ESCALATE_VISUAL"
        assert pres.slides[0].disposition_reason == "PICTURE_HEAVY_LOW_TEXT_SLIDE"
        assert "slide1#shape20" in pres.slides[0].flagged_shape_locators

    def test_cached_chart_remains_text_only(self) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Quarterly Revenue Chart</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:graphicFrame>
        <p:nvGraphicFramePr><p:cNvPr id="5" name="Chart 1"/><p:nvPr/></p:nvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
            <c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rIdChart"/>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        chart_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <c:chart>
    <c:title><c:tx><c:rich><a:p><a:r><a:t>Revenue By Region</a:t></a:r></a:p></c:rich></c:tx></c:title>
    <c:plotArea>
      <c:barChart>
        <c:ser>
          <c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>North America</c:v></c:pt></c:strCache></c:strRef></c:tx>
          <c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>Q1</c:v></c:pt><c:pt idx="1"><c:v>Q2</c:v></c:pt></c:strCache></c:strRef></c:cat>
          <c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>1200000</c:v></c:pt><c:pt idx="1"><c:v>1450000</c:v></c:pt></c:numCache></c:numRef></c:val>
        </c:ser>
      </c:barChart>
    </c:plotArea>
  </c:chart>
</c:chartSpace>"""
        rels = {
            1: [
                {
                    "id": "rIdChart",
                    "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart",
                    "target": "../charts/chart1.xml",
                }
            ]
        }
        raw = _build_test_pptx([slide1_xml], slide_rels=rels, extra_parts={"ppt/charts/chart1.xml": chart_xml})
        pres = extract_pptx_presentation(raw)

        assert pres.slide_count == 1
        assert len(pres.slides[0].chart_data) == 1
        assert pres.slides[0].chart_data[0]["is_accessible"] is True
        assert pres.slides[0].chart_data[0]["title"] == "Revenue By Region"
        assert pres.slides[0].disposition == "TEXT_ONLY"
        assert pres.slides[0].disposition_reason == "TEXT_NATIVE_CONFIRMED"

    def test_inaccessible_chart_becomes_hitl_required(self) -> None:
        # Chart with missing series points/values
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:graphicFrame>
        <p:nvGraphicFramePr><p:cNvPr id="8" name="Empty Chart"/><p:nvPr/></p:nvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
            <c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rIdChart"/>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        chart_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
  <c:chart>
    <c:title><c:tx><c:rich><a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:r><a:t>Broken Chart</a:t></a:r></a:p></c:rich></c:tx></c:title>
    <c:plotArea><c:barChart/></c:plotArea>
  </c:chart>
</c:chartSpace>"""
        rels = {
            1: [
                {
                    "id": "rIdChart",
                    "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart",
                    "target": "../charts/chart1.xml",
                }
            ]
        }
        raw = _build_test_pptx([slide1_xml], slide_rels=rels, extra_parts={"ppt/charts/chart1.xml": chart_xml})
        pres = extract_pptx_presentation(raw)

        assert pres.slide_count == 1
        assert pres.hitl_slides == [1]
        assert pres.slides[0].disposition == "HITL_REQUIRED"
        assert pres.slides[0].disposition_reason == "INACCESSIBLE_CHART_OR_UNSUPPORTED_DATA"
        assert "slide1#shape8" in pres.slides[0].flagged_shape_locators

    def test_geometric_reading_order_sorting(self) -> None:
        # Shape 1 is physically at bottom: y=500000. Shape 2 is physically at top: y=100000.
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Bottom Shape"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="100000" y="500000"/><a:ext cx="200000" cy="50000"/></a:xfrm></p:spPr>
        <p:txBody><a:p><a:r><a:t>Bottom text content.</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Top Shape"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="100000" y="100000"/><a:ext cx="200000" cy="50000"/></a:xfrm></p:spPr>
        <p:txBody><a:p><a:r><a:t>Top header content.</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        raw = _build_test_pptx([slide1_xml])
        pres = extract_pptx_presentation(raw)

        # Shapes must be sorted top-to-bottom so 'Top header content' is processed first as title
        assert pres.slides[0].title == "Top header content."
        assert pres.slides[0].paragraphs == ["Bottom text content."]

    def test_hyperlink_extraction(self) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody>
          <a:p>
            <a:r>
              <a:rPr><a:hlinkClick r:id="rIdLink"/></a:rPr>
              <a:t>External Knowledge Portal</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        rels = {
            1: [
                {
                    "id": "rIdLink",
                    "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                    "target": "https://company.internal/docs",
                    "target_mode": "External",
                }
            ]
        }
        raw = _build_test_pptx([slide1_xml], slide_rels=rels)
        pres = extract_pptx_presentation(raw)

        assert len(pres.slides[0].hyperlinks) == 1
        assert pres.slides[0].hyperlinks[0] == "https://company.internal/docs"

    def test_pptx_source_units_generation(self) -> None:
        slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody><a:p><a:r><a:t>Executive Briefing</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>"""
        raw = _build_test_pptx([slide1_xml])
        pres = extract_pptx_presentation(raw)
        units = build_pptx_source_units(pres, "fedcba9876543210")

        assert len(units) == 1
        assert units[0].locator == "slide1"
        assert units[0].unit_type == "slide"
        assert units[0].format == "pptx"
        assert "Executive Briefing" in units[0].content
        assert units[0].metadata["disposition"] == "TEXT_ONLY"
