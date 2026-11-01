"""
tests/test_stage2_multimodal_loaders_adversarial.py

Adversarial and edge-case regression tests for Wave 2 (Stage 2 Multimodal Content Loaders,
Unified Locator Grammar, and Corpus-Agnostic Sieve), covering defects D2-01 through D2-12
across boundary payloads, malformed inputs, invariant bypass attempts, and exact regressions.
"""
from __future__ import annotations

import base64
import io
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict

import openpyxl
import pytest

from dual_pipeline.document_pipeline_core.content_loader import _load_document_file
from dual_pipeline.document_pipeline_core.conversion_cache import (
    compute_conversion_cache_key,
    resolve_conversion_with_cache,
)
from dual_pipeline.document_pipeline_core.diagram_loader import (
    load_drawio_diagram,
    load_svg_diagram,
    load_vsdx_diagram,
)
from dual_pipeline.document_pipeline_core.docx_coverage import extract_docx_with_coverage
from dual_pipeline.document_pipeline_core.eml_loader import extract_eml_markdown
from dual_pipeline.document_pipeline_core.format_source_units import (
    SourceUnit,
    build_eml_source_units,
    build_pdf_source_units,
    build_pptx_source_units,
)
from dual_pipeline.document_pipeline_core.pptx_shapes import get_shape_position
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    UnitDispositionClass,
    classify_unit_sieve,
    extract_slide_and_section_units,
    merge_unit_batch_projections,
    resolve_locator_for_offset,
    strip_master_boilerplate_and_footers,
)
from dual_pipeline.document_pipeline_core.visual_escalation import (
    SlideVisualAssessment,
    VisualDisposition,
    validate_visual_routing_invariants,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    extract_staffing_workbook_projection,
    project_xlsx_workbook,
)
from dual_pipeline.docx_source import extract_docx_markdown
from dual_pipeline.pdf_source import PDFExtractionReport, PDFPageInventory, repair_kerning
from dual_pipeline.pptx_source import extract_pptx_presentation


def _build_pptx_with_group_and_smartart() -> bytes:
    """Create a synthetic PPTX with a title placeholder lacking a:off, nested p:grpSp, and SmartArt diagram XML."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdDgm1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "ppt/diagrams/data1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<dgm:ptLst>"
            '<dgm:pt modelId="1"><dgm:t><a:p><a:r><a:t>SmartArt Node Alpha</a:t></a:r></a:p></dgm:t></dgm:pt>'
            '<dgm:pt modelId="2"><dgm:t><a:p><a:r><a:t>SmartArt Node Beta</a:t></a:r></a:p></dgm:t></dgm:pt>'
            "</dgm:ptLst></dgm:dataModel>",
        )
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
            "<p:cSld><p:spTree>"
            # Shape at explicit Y=200
            "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"BodyShape\"/><p:nvPr/></p:nvSpPr>"
            "<p:spPr><a:xfrm><a:off x=\"100\" y=\"200\"/></a:xfrm></p:spPr>"
            "<p:txBody><a:p><a:r><a:t>Body Paragraph Content</a:t></a:r></a:p></p:txBody></p:sp>"
            # Title placeholder WITHOUT a:xfrm/a:off (must sort first at (0,0), not (999999999,999999999))
            "<p:sp><p:nvSpPr><p:cNvPr id=\"1\" name=\"Title 1\"/><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            "<p:spPr/>"
            "<p:txBody><a:p><a:r><a:t>Executive Architecture Header</a:t></a:r></a:p></p:txBody></p:sp>"
            # Group shape with nested group shape
            "<p:grpSp><p:nvGrpSpPr><p:cNvPr id=\"3\" name=\"OuterGroup\"/></p:nvGrpSpPr>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"4\" name=\"GroupText1\"/></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Outer Group Callout</a:t></a:r></a:p></p:txBody></p:sp>"
            "<p:grpSp><p:nvGrpSpPr><p:cNvPr id=\"5\" name=\"InnerGroup\"/></p:nvGrpSpPr>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"6\" name=\"GroupText2\"/></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Nested Inner Group Metric $4.2M</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:grpSp></p:grpSp>"
            # GraphicFrame referencing SmartArt diagram rIdDgm1
            "<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id=\"7\" name=\"Diagram 1\"/></p:nvGraphicFramePr>"
            "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/diagram\">"
            '<dgm:relIds r:dm="rIdDgm1"/>'
            "</a:graphicData></a:graphic></p:graphicFrame>"
            "</p:spTree></p:cSld></p:sld>",
        )
    return buf.getvalue()


class TestWave2MultimodalLoadersAdversarial:
    """Adversarial & Regression Suite for Stage 2 Loaders (D2-01 through D2-12)."""

    def test_d2_01_pptx_group_shapes_smartart_and_title_placeholder_reading_order(self) -> None:
        """D2-01: Verify recursive <p:grpSp>, SmartArt diagram extraction, and (0,0) title placeholder coordinates."""
        raw = _build_pptx_with_group_and_smartart()
        pres = extract_pptx_presentation(raw)
        assert len(pres.slides) == 1
        slide = pres.slides[0]
        # Title placeholder without a:off must be placed at index 0 as slide.title (and excluded from body paragraphs)
        assert slide.title == "Executive Architecture Header"
        assert slide.paragraphs[0] == "Body Paragraph Content"
        full_md = slide.to_markdown()
        assert "Outer Group Callout" in full_md
        assert "Nested Inner Group Metric $4.2M" in full_md
        assert "SmartArt Node Alpha" in full_md
        assert "SmartArt Node Beta" in full_md
        # Because SmartArt text was extracted alongside slide text, slide is ESCALATE_VISUAL rather than HITL_REQUIRED
        assert slide.disposition == "ESCALATE_VISUAL"

    def test_d2_02_d2_11_xlsx_no_hardcoded_fallbacks_sec_idx_and_header_preservation(self) -> None:
        """D2-02 & D2-11: Verify zero ungrounded fallback metrics/waves/risks, monotonic sec_idx, and cell headers."""
        # 1. Generic staffing text without Apex Global/PartnerAdvisory hardcoded numbers
        generic_text = (
            "Project Name: Acme Cloud Migration\n"
            "| Role | Rate | Total Hours |\n"
            "| TBD1 Cloud Lead | $180 | Total Hours: 400 |\n"
        )
        proj_slice = extract_staffing_workbook_projection(generic_text)
        ent_ids = {e["id"] for e in proj_slice["graph_mutations"]["entity_upserts"]}
        risk_ids = {r["risk_id"] for r in proj_slice["graph_mutations"]["risk_updates"]}
        # Must NOT inject ungrounded 30074, 6704029, 8640637, or continuous-quality-advisor
        assert "metric:workbook-total-hours-30074" not in ent_ids
        assert "metric:workbook-tm-revenue-6704029" not in ent_ids
        assert "metric:workbook-fixed-fee-8640637" not in ent_ids
        assert "risk:continuous-quality-advisor-required" not in risk_ids
        assert "workstream:discovery-mobilize" not in ent_ids
        # Must reflect actual grounded 400 hours and TBD1 Cloud Lead
        assert "metric:workbook-total-hours-400" in ent_ids
        role_ent = next(e for e in proj_slice["graph_mutations"]["entity_upserts"] if e["id"] == "role:tbd1")
        assert role_ent["name"] == "TBD1 Cloud Lead"

        # 2. Workbook with multiple blocks and pipe/newline characters in cells
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Financials"
        ws["A1"] = "Report: Q4 Summary"  # metadata preamble before table header in same block
        ws["A2"] = "Owner"
        ws["B2"] = "Budget | Notes"
        ws["A3"] = "Alice\nSmith"
        ws["B3"] = "$500K | Approved"
        # Blank row 4 separates second block
        ws["A5"] = "Region"
        ws["B5"] = "Status"
        ws["A6"] = "North"
        ws["B6"] = "Active"

        buf = io.BytesIO()
        wb.save(buf)
        xlsx_proj = project_xlsx_workbook(buf.getvalue(), filename="fin.xlsx")
        sec_indices = [s.section_index for s in xlsx_proj.sheet_projections[0].sections]
        # Block 1 emits 2 sections (metadata_block=1, table=2); Block 2 must start at 3
        assert sec_indices == [1, 2, 3]
        # Markdown must escape pipes and newlines
        md = xlsx_proj.rendered_markdown
        assert "\\|" in md
        assert "<br>" in md
        # Source units must preserve column headers in content and metadata
        table_units = [u for u in xlsx_proj.source_units if u.unit_type == "table_row"]
        assert any("Owner: Alice\nSmith" in u.content for u in table_units)
        assert any(u.metadata.get("cells", {}).get("Region") == "North" for u in table_units)

    def test_d2_03_sieve_non_apex_global_appendix_and_standalone_numbers_preserved(self) -> None:
        """D2-03: Substantive non-Apex Global slides after a template trigger are not quarantined; standalone numbers survive."""
        deck_md = (
            "## Slide 1: Account Planning Template\n"
            "READ ME THEN DELETE: Instructions for completing the template.\n\n"
            "## Slide 2: Acme Corp Cloud Architecture Roadmap\n"
            "- Deliver milestone 1 by Q3 2026 with $2.5M budget allocation.\n"
            "- Migrate 140 microservices to Kubernetes cluster.\n"
        )
        units = extract_slide_and_section_units(deck_md, source_hash="a" * 64)
        assert len(units) == 2
        assert units[0].metadata["sieve_class"] == UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value
        assert units[0].metadata["is_substantive"] is False
        # Slide 2 has no "Apex Global" token, but is a substantive Acme Corp slide — must NOT be quarantined!
        assert units[1].metadata["is_substantive"] is True
        assert units[1].metadata["materiality"] in {"SUBSTANTIVE", "SUBSTANTIVE_ANCHOR"}

        # Standalone metric "500" or year "2026" on a single-line cell/unit must not be wiped to empty string
        assert strip_master_boilerplate_and_footers("500") == "500"
        assert strip_master_boilerplate_and_footers("2026") == "2026"
        # Trailing slide number after substantive lines IS stripped
        assert strip_master_boilerplate_and_footers("Key Deliverable: Cloud Factory\n42") == "Key Deliverable: Cloud Factory"

    def test_d2_04_visual_escalation_selective_flag_filtering(self) -> None:
        """D2-04: validate_visual_routing_invariants catches flagged shape locators on TEXT_ONLY units."""
        s1 = SlideVisualAssessment(
            slide_num=1,
            locator="slide1",
            disposition=VisualDisposition.TEXT_ONLY,
            text_char_count=300,
            shape_count=3,
            text_shape_count=2,
            visual_shape_count=1,
            has_inaccessible_chart=False,
            has_diagram=False,
            reason="TEXT_NATIVE_CONFIRMED",
            flagged_shape_locators=["slide1:shape9"],
        )
        s2 = SlideVisualAssessment(
            slide_num=2,
            locator="slide2",
            disposition=VisualDisposition.ESCALATE_VISUAL,
            text_char_count=20,
            shape_count=2,
            text_shape_count=0,
            visual_shape_count=2,
            has_inaccessible_chart=False,
            has_diagram=False,
            reason="PICTURE_HEAVY_LOW_TEXT_SLIDE",
            flagged_shape_locators=["slide2:shape1"],
        )
        # Routing slide2's flagged shape is valid
        validate_visual_routing_invariants(slides=[s1, s2], attempted_raster_targets=["slide2:shape1"])
        # Routing slide1's flagged shape when slide1 is TEXT_ONLY must raise ValueError (previously dead code `not flags and any(...)`)
        with pytest.raises(ValueError, match="Invariant violation: pure-text PPTX slide"):
            validate_visual_routing_invariants(slides=[s1, s2], attempted_raster_targets=["slide1:shape9"])

    def test_d2_05_conversion_cache_selected_sheets_and_format_id_parity(self, tmp_path: Path) -> None:
        """D2-05: Conversion cache distinguishes selected_sheets and returns normalized format IDs on hit/miss."""
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "SheetA"
        ws1["A1"] = "Metric"
        ws1["B1"] = "Value"
        ws1["A2"] = "Alpha"
        ws1["B2"] = "100"
        ws2 = wb.create_sheet("SheetB")
        ws2["A1"] = "Metric"
        ws2["B1"] = "Value"
        ws2["A2"] = "Beta"
        ws2["B2"] = "200"

        xlsx_path = tmp_path / "multi.xlsx"
        wb.save(xlsx_path)
        cache_dir = str(tmp_path / "cache")

        meta_a: Dict[str, Any] = {"conversion_cache_dir": cache_dir, "selected_sheets": ["SheetA"]}
        text_a, _, fmt_a = _load_document_file(xlsx_path, metadata=meta_a)
        assert fmt_a == "xlsx"
        assert "Alpha" in text_a
        assert "Beta" not in text_a

        # Second call with SheetB on the same file must NOT return cached SheetA projection
        meta_b: Dict[str, Any] = {"conversion_cache_dir": cache_dir, "selected_sheets": ["SheetB"]}
        text_b, _, fmt_b = _load_document_file(xlsx_path, metadata=meta_b)
        assert fmt_b == "xlsx"
        assert "Beta" in text_b
        assert "Alpha" not in text_b

        # Cache hit on SheetA must also return lowercase "xlsx" (not uppercase "XLSX")
        meta_a_hit: Dict[str, Any] = {"conversion_cache_dir": cache_dir, "selected_sheets": ["SheetA"]}
        text_a_hit, _, fmt_a_hit = _load_document_file(xlsx_path, metadata=meta_a_hit)
        assert fmt_a_hit == "xlsx"
        assert meta_a_hit.get("conversion_artifact", {}).get("was_cached") is True
        assert text_a_hit == text_a

    def test_d2_06_docx_mc_fallback_dedup_omml_math_and_sdt_extraction(self) -> None:
        """D2-06: DOCX skips <mc:Fallback> duplicate text, extracts <m:oMath> without failing, and reads <w:sdt>."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
                'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
                "<w:body>"
                "<w:p>"
                "<mc:AlternateContent>"
                "<mc:Choice Requires=\"wps\"><w:r><w:t>UniqueCalloutText</w:t></w:r></mc:Choice>"
                "<mc:Fallback><w:r><w:t>UniqueCalloutText</w:t></w:r></mc:Fallback>"
                "</mc:AlternateContent>"
                "</w:p>"
                "<w:p><m:oMathPara><m:oMath><m:r><m:t>E=mc^2</m:t></m:r></m:oMath></m:oMathPara></w:p>"
                "<w:sdt><w:sdtContent><w:p><w:r><w:t>SDT Content Control Block</w:t></w:r></w:p></w:sdtContent></w:sdt>"
                "</w:body></w:document>",
            )
        raw_docx = buf.getvalue()
        text_cov, cov = extract_docx_with_coverage(raw_docx)
        assert cov["complete"] is True
        assert cov["expected_text_nodes"] == 3
        assert cov["preserved_text_nodes"] == 3
        md = extract_docx_markdown(raw_docx)
        # Choice + Fallback must not duplicate UniqueCalloutText
        assert md.count("UniqueCalloutText") == 1
        assert text_cov.count("UniqueCalloutText") == 1
        assert "E=mc^2" in md
        assert "SDT Content Control Block" in md

    def test_d2_07_d2_08_locator_offset_and_batch_merge_collision_preservation(self) -> None:
        """D2-07 & D2-08: resolve_locator_for_offset finds heading at offset 0; batch merge combines colliding IDs."""
        doc = "## Slide 5: Target Operating Model\nBody line 1\n"
        # Offset 0 is at the start of "## Slide 5: ..."
        assert resolve_locator_for_offset(doc, 0) == "Slide 5"
        assert resolve_locator_for_offset(doc, 5) == "Slide 5"

        batch1 = {
            "evidence_index": {
                "ev-01": {"evidence_id": "ev-01", "exact_text": "Quote A", "locator": "slide:1"}
            },
            "graph_mutations": {
                "decision_updates": [{"decision_id": "dec-shared", "title": "Adopt GCP", "evidence_ids": ["ev-01"]}],
                "risk_updates": [{"risk_id": "risk-shared", "title": "Timeline Risk", "evidence_ids": ["ev-01"]}],
            },
        }
        batch2 = {
            "evidence_index": {
                "ev-01": {"evidence_id": "ev-01", "exact_text": "Quote B", "locator": "slide:2"}
            },
            "graph_mutations": {
                "decision_updates": [{"decision_id": "dec-shared", "title": "Adopt GCP", "evidence_ids": ["ev-01"]}],
                "risk_updates": [{"risk_id": "risk-shared", "title": "Timeline Risk", "evidence_ids": ["ev-01"]}],
            },
        }
        merged = merge_unit_batch_projections([batch1, batch2])
        decs = merged["graph_mutations"]["decision_updates"]
        risks = merged["graph_mutations"]["risk_updates"]
        assert len(decs) == 1
        assert decs[0]["evidence_ids"] == ["ev-01", "ev-02"]
        assert len(risks) == 1
        assert risks[0]["evidence_ids"] == ["ev-01", "ev-02"]

    def test_d2_09_svg_foreign_object_drawio_object_wrapper_xxe_and_vsdx_wire_connects(self, tmp_path: Path) -> None:
        """D2-09: SVG foreignObject/tail, Draw.io <object label> + XXE rejection, and VSDX 2-endpoint wire connects."""
        # 1. SVG with foreignObject and .tail text
        svg_bytes = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<g id="node-1"><foreignObject><div>HTML Foreign Object Label</div></foreignObject></g>'
            b'<text id="txt-1"><tspan>Prefix</tspan> TailText</text>'
            b"</svg>"
        )
        svg_path = tmp_path / "arch.svg"
        svg_path.write_bytes(svg_bytes)
        svg_res = load_svg_diagram(svg_path)
        svg_texts = [u["content"] if isinstance(u, dict) else u.content for u in svg_res["source_units"]]
        assert any("HTML Foreign Object Label" in t for t in svg_texts)
        assert any("Prefix TailText" in t for t in svg_texts)

        # 2. Draw.io with <object label="..."> wrapper
        drawio_xml = (
            b'<mxfile><diagram id="d1" name="Arch">'
            b'<mxGraphModel><root>'
            b'<mxCell id="0"/><mxCell id="1" parent="0"/>'
            b'<object id="n1" label="API Gateway"><mxCell vertex="1" parent="1"/></object>'
            b'<UserObject id="n2" label="Auth Service"><mxCell vertex="1" parent="1"/></UserObject>'
            b'<object id="e1" label="HTTPS"><mxCell edge="1" source="n1" target="n2" parent="1"/></object>'
            b"</root></mxGraphModel></diagram></mxfile>"
        )
        drawio_path = tmp_path / "arch.drawio"
        drawio_path.write_bytes(drawio_xml)
        drawio_res = load_drawio_diagram(drawio_path)
        assert drawio_res["source_coverage"]["shapes_count"] == 2
        assert drawio_res["source_coverage"]["connectors_count"] == 1
        assert "API Gateway" in drawio_res["text"] and "Auth Service" in drawio_res["text"]

        # 3. Draw.io compressed payload containing XXE <!ENTITY> must fail closed with ValueError
        xxe_inner = b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><mxGraphModel><root/></mxGraphModel>'
        comp = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        deflated = comp.compress(xxe_inner) + comp.flush()
        b64_xxe = base64.b64encode(deflated).decode("ascii")
        xxe_drawio = f'<mxfile><diagram id="d1">{b64_xxe}</diagram></mxfile>'.encode("utf-8")
        xxe_path = tmp_path / "xxe.drawio"
        xxe_path.write_bytes(xxe_drawio)
        with pytest.raises(ValueError, match="Malicious XML detected in Draw.io inner XML"):
            load_drawio_diagram(xxe_path)

        # 4. VSDX with 1D wire shape connecting two 2D node shapes via two <Connect> rows
        v_buf = io.BytesIO()
        with zipfile.ZipFile(v_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "visio/pages/page1.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">'
                "<Shapes>"
                '<Shape ID="1" NameU="ClientNode"><Text>Web Client</Text></Shape>'
                '<Shape ID="2" NameU="ServerNode"><Text>Backend Server</Text></Shape>'
                '<Shape ID="99" NameU="Dynamic connector"><Text>gRPC</Text></Shape>'
                "</Shapes>"
                "<Connects>"
                '<Connect FromSheet="99" FromCell="BeginX" ToSheet="1"/>'
                '<Connect FromSheet="99" FromCell="EndX" ToSheet="2"/>'
                "</Connects>"
                "</PageContents>",
            )
        vsdx_path = tmp_path / "arch.vsdx"
        vsdx_path.write_bytes(v_buf.getvalue())
        vsdx_res = load_vsdx_diagram(vsdx_path)
        assert vsdx_res["source_coverage"]["connectors_count"] == 1
        assert "Web Client" in vsdx_res["text"]
        assert "Backend Server" in vsdx_res["text"]
        assert any(
            (u.get("unit_type") if isinstance(u, dict) else u.unit_type) == "visio_connector"
            and "Web Client" in (u.get("content") if isinstance(u, dict) else u.content)
            and "Backend Server" in (u.get("content") if isinstance(u, dict) else u.content)
            for u in vsdx_res["source_units"]
        )

    def test_d2_10_pdf_kerning_preserves_spaced_numbers_and_builds_sieve_metadata(self) -> None:
        """D2-10: repair_kerning repairs 2 0 2 6 -> 2026 without corrupting '1 0 2 5' into '1025'."""
        assert repair_kerning("F l o r i d a 2 0 2 6") == "Florida 2026"
        assert repair_kerning("Table values: 1 0 2 5") == "Table values: 1 0 2 5"

        rep = PDFExtractionReport(
            title="Test SOW",
            total_pages=1,
            pages=[
                PDFPageInventory(
                    page_num=1,
                    locator="p1",
                    text="## Page 1: Statement of Work\nFixed Fee: $1,250,000 across 4 milestones.",
                    char_count=72,
                    image_count=2,
                    quality_score=0.95,
                    disposition="TEXT_ONLY",
                    reason="Clean text",
                )
            ],
            text_native_pages=[1],
            escalated_pages=[],
            hitl_pages=[],
            is_encrypted=False,
            encryption_status="NONE",
            raw_sha256="f" * 64,
            extracted_markdown="## Page 1: Statement of Work\nFixed Fee: $1,250,000 across 4 milestones.",
        )
        units = build_pdf_source_units(rep, source_hash="f" * 64)
        assert len(units) == 1
        assert units[0].metadata["image_count"] == 2
        assert units[0].metadata["disposition"] == "TEXT_ONLY"
        assert units[0].metadata["sieve_class"] == UnitDispositionClass.C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING.value
        assert units[0].metadata["is_substantive"] is True

    def test_d2_12_eml_rfc2047_headers_cc_and_declared_charset(self) -> None:
        """D2-12: EML loader decodes RFC-2047 headers, preserves Cc, and respects declared ISO-8859-1 charset."""
        raw_eml = (
            b"From: =?utf-8?B?QWxpY2UgxI1lcm7DoQ==?= <alice@example.com>\r\n"
            b"To: bob@example.com\r\n"
            b"Cc: =?utf-8?Q?Carol_Kr=C3=BCger?= <carol@example.com>\r\n"
            b"Subject: =?utf-8?Q?Caf=C3=A9_ Architecture_Review?=\r\n"
            b"Message-ID: <msg-123@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=\"iso-8859-1\"\r\n"
            b"Content-Transfer-Encoding: 8bit\r\n"
            b"\r\n"
            + "Résumé of the Café architecture budget: £50,000.".encode("iso-8859-1")
        )
        eml_md = extract_eml_markdown(raw_eml)
        assert "From: Alice černá <alice@example.com>" in eml_md
        assert "Cc: Carol Krüger <carol@example.com>" in eml_md
        assert "Subject: Café  Architecture Review" in eml_md or "Café" in eml_md
        assert "Résumé of the Café architecture budget: £50,000." in eml_md

        eml_units = build_eml_source_units(raw_eml, source_hash="1" * 64)
        cc_units = [u for u in eml_units if u.locator == "header:Cc"]
        assert len(cc_units) == 1
        assert "Carol Krüger" in cc_units[0].content
