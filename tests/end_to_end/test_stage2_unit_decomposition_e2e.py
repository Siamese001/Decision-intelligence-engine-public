"""
tests/end_to_end/test_stage2_unit_decomposition_e2e.py

End-to-End integration tests for Wave 2 (Stage 2 Multimodal Content Loaders,
Unified Locator Grammar, and Corpus-Agnostic Sieve). Verifies multimodal loading,
conversion cache parity across metadata options and slide_units, unit decomposition,
sieve filtering, and multi-batch projection consolidation.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pytest

from dual_pipeline.document_pipeline_core.content_loader import _load_document_file
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    UnitDispositionClass,
    extract_slide_and_section_units,
    merge_unit_batch_projections,
    partition_units_into_batches,
    resolve_locator_for_offset,
)
from dual_pipeline.document_pipeline_core.xlsx_projection import (
    extract_staffing_workbook_projection,
)


def _create_e2e_pptx(path: Path) -> None:
    """Write a 3-slide PPTX deck with SmartArt, group shapes, and a template appendix slide."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<p:sldIdLst>"
            '<p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId2"/>'
            '<p:sldId id="258" r:id="rId3"/>'
            "</p:sldIdLst></p:presentation>",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide3.xml"/>'
            "</Relationships>",
        )
        # Slide 1: Substantive SOW & Commercial slide with Group Shape
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"1\" name=\"Title 1\"/><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Statement of Work &amp; Commercial Summary</a:t></a:r></a:p></p:txBody></p:sp>"
            "<p:grpSp><p:nvGrpSpPr><p:cNvPr id=\"2\" name=\"CommercialGroup\"/></p:nvGrpSpPr>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"FeeBox\"/></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Fixed Fee: $3,400,000 across 3 delivery milestones in 2026.</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:grpSp>"
            "</p:spTree></p:cSld></p:sld>",
        )
        # Slide 2: Template Appendix trigger (quarantined)
        zf.writestr(
            "ppt/slides/slide2.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"1\" name=\"Title 1\"/><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Account Planning Template</a:t></a:r></a:p></p:txBody></p:sp>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Body\"/></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>READ ME THEN DELETE: Insert client name and placeholder text here.</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        )
        # Slide 3: Non-Apex Global substantive architecture slide after template trigger (must NOT be quarantined)
        zf.writestr(
            "ppt/slides/slide3.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"1\" name=\"Title 1\"/><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>Global Retail Target Architecture Roadmap</a:t></a:r></a:p></p:txBody></p:sp>"
            "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Body\"/></p:nvSpPr>"
            "<p:txBody><a:p><a:r><a:t>- Milestone 1: Deploy Domain Interface Specifications by Q2 2026 ($1.2M).</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        )
    path.write_bytes(buf.getvalue())


class TestStage2UnitDecompositionE2E:
    """End-to-End verification of Stage 2 Multimodal Loading, Sieve, Cache, and Batch Merging."""

    def test_e2e_pptx_loading_sieve_batching_and_cache_restoration(self, tmp_path: Path) -> None:
        """Verify end-to-end PPTX loading, slide unit sieve classification, batching, and cache restoration."""
        pptx_path = tmp_path / "enterprise_proposal.pptx"
        _create_e2e_pptx(pptx_path)
        cache_dir = str(tmp_path / "conv_cache")

        # 1. Cold load with cache enabled
        meta_cold: Dict[str, Any] = {"conversion_cache_dir": cache_dir}
        text_cold, _, fmt_cold = _load_document_file(pptx_path, metadata=meta_cold)
        assert fmt_cold == "pptx"
        assert meta_cold.get("conversion_artifact", {}).get("was_cached") is False
        assert "Fixed Fee: $3,400,000" in text_cold

        units_cold = meta_cold.get("source_units") or []
        assert len(units_cold) == 3
        # Slide 1 is C3 commercial SOW
        assert units_cold[0]["metadata"]["sieve_class"] == UnitDispositionClass.C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING.value
        assert units_cold[0]["metadata"]["is_substantive"] is True
        # Slide 2 is Q1 template scaffold
        assert units_cold[1]["metadata"]["sieve_class"] == UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value
        assert units_cold[1]["metadata"]["is_substantive"] is False
        # Slide 3 is C4 architecture anchor (substantive despite following Slide 2 and lacking 'Apex Global')
        assert units_cold[2]["metadata"]["sieve_class"] == UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value
        assert units_cold[2]["metadata"]["materiality"] == "SUBSTANTIVE_ANCHOR"
        assert units_cold[2]["metadata"]["is_substantive"] is True

        # 2. Warm cache hit must restore source_units, slide_units, and normalized format_id
        meta_warm: Dict[str, Any] = {"conversion_cache_dir": cache_dir}
        text_warm, _, fmt_warm = _load_document_file(pptx_path, metadata=meta_warm)
        assert fmt_warm == "pptx"
        assert meta_warm.get("conversion_artifact", {}).get("was_cached") is True
        assert text_warm == text_cold
        assert meta_warm.get("source_units") == units_cold

        # 3. Partition units into batches (quarantined Slide 2 must be excluded)
        batches = partition_units_into_batches(units_cold, batch_size=1)
        assert len(batches) == 2
        batch_locators = [b[0]["locator"] for b in batches]
        assert batch_locators == ["slide1", "slide3"]

        # 4. Merge batch projections with colliding entity/decision/risk IDs
        b1_proj = {
            "evidence_index": {
                "ev-01": {
                    "evidence_id": "ev-01",
                    "exact_text": "Fixed Fee: $3,400,000 across 3 delivery milestones in 2026.",
                    "locator": {"kind": "slide", "value": "slide1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [{"id": "workstream:retail-arch", "type": "workstream", "name": "Retail Architecture", "evidence_ids": ["ev-01"]}],
                "decision_updates": [{"decision_id": "dec:fixed-fee-path", "title": "Approve Fixed Fee SOW", "evidence_ids": ["ev-01"]}],
            },
        }
        b2_proj = {
            "evidence_index": {
                "ev-01": {
                    "evidence_id": "ev-01",
                    "exact_text": "Milestone 1: Deploy Domain Interface Specifications by Q2 2026 ($1.2M).",
                    "locator": {"kind": "slide", "value": "slide3"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [{"id": "workstream:retail-arch", "type": "workstream", "name": "Retail Architecture", "evidence_ids": ["ev-01"]}],
                "decision_updates": [{"decision_id": "dec:fixed-fee-path", "title": "Approve Fixed Fee SOW", "evidence_ids": ["ev-01"]}],
            },
        }
        merged = merge_unit_batch_projections([b1_proj, b2_proj])
        assert len(merged["evidence_index"]) == 2
        ents = merged["graph_mutations"]["entity_upserts"]
        assert len(ents) == 1
        assert ents[0]["evidence_ids"] == ["ev-01", "ev-02"]
        decs = merged["graph_mutations"]["decision_updates"]
        assert len(decs) == 1
        assert decs[0]["evidence_ids"] == ["ev-01", "ev-02"]

    def test_e2e_xlsx_and_markdown_slide_decomposition_with_cache(self, tmp_path: Path) -> None:
        """Verify Markdown slide decomposition and XLSX staffing slice end-to-end."""
        md_path = tmp_path / "strategy_deck.md"
        md_content = (
            "## Executive Summary\n"
            "High-level overview of the 2026 transformation.\n\n"
            "## Slide 1: Commercial Staffing Model\n"
            "- Statement of Work Fixed Fee: $1,800,000\n"
            "- TBD1 Principal Architect | $250/hr | Total Hours: 800\n\n"
            "## Slide 2: Target Cloud Architecture\n"
            "- Domain Interface Specifications and Self-Service Journey Map\n"
        )
        md_path.write_text(md_content, encoding="utf-8")
        cache_dir = str(tmp_path / "md_cache")

        meta1: Dict[str, Any] = {"conversion_cache_dir": cache_dir}
        loaded_text, _, fmt1 = _load_document_file(md_path, metadata=meta1)
        assert fmt1 == "md"
        assert len(meta1.get("slide_units") or []) == 3

        # Warm cache hit must preserve slide_units and normalized format_id "md"
        meta2: Dict[str, Any] = {"conversion_cache_dir": cache_dir}
        _, _, fmt2 = _load_document_file(md_path, metadata=meta2)
        assert fmt2 == "md"
        assert meta2.get("conversion_artifact", {}).get("was_cached") is True
        assert meta2.get("slide_units") == meta1.get("slide_units")

        # Verify locator resolution at heading start and inside slide body
        s1_offset = loaded_text.index("## Slide 1:")
        assert resolve_locator_for_offset(loaded_text, s1_offset) == "Slide 1"
        s2_offset = loaded_text.index("Self-Service Journey Map")
        assert resolve_locator_for_offset(loaded_text, s2_offset) == "Slide 2"

        # Verify staffing workbook projection on the loaded text emits grounded metrics only
        wb_slice = extract_staffing_workbook_projection(loaded_text)
        ent_ids = {e["id"] for e in wb_slice["graph_mutations"]["entity_upserts"]}
        assert "metric:workbook-fixed-fee-1800000" in ent_ids
        assert "metric:workbook-total-hours-800" in ent_ids
        assert "metric:workbook-total-hours-30074" not in ent_ids
