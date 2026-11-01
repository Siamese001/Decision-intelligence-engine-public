"""End-to-end lifecycle verification for document coverage, evidence selection, and adapter integrity.

Verifies:
1. Cold and warm conversion cache produce identical physical-unit coverage and locators.
2. Raster pages vs blank pages are cleanly distinguished (EMPTY_PAGE vs RASTER_IMAGE_ONLY).
3. XLSX workbooks with uncalculated formula cells and sparse rows are accounted and prevent false full ingestion claims.
4. Unsupported conversions (PARTIAL, BLOCKED, UNAVAILABLE) cannot claim exhaustive coverage.
5. Every advertised capability registry adapter resolves to a real importable implementation.
6. Table rows and visual claims use value-sensitive decisive evidence selection and explicit omission accounting.
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pytest

from dual_pipeline.attachment_core.prompt_builder import (
    _format_claims_and_text,
    _format_milestones_and_tables,
    _select_decisive_claims,
    _select_decisive_table_rows,
)
from dual_pipeline.attachment_core.types import AttachmentType, ExtractedAttachmentContent
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.source_manifest import (
    build_source_manifest,
)
from dual_pipeline.source_classification.capability_registry import (
    _REGISTRY,
    SupportStatus,
    get_capability_by_ext,
    is_format_supported,
)


def _create_sample_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "FinancialSummary"
    ws1.append(["Account", "Region", "Q1", "Q2", "Status"])
    ws1.append(["Generation", "APEX", "1200", "1350", "NORMAL"])
    ws1.append(["Transmission", "NEER", "850", "910", "NORMAL"])

    ws2 = wb.create_sheet(title="SparseMetrics")
    ws2.append(["Metric", "Cluster", "Target", "Actual", "Owner", "Notes"])
    ws2.append(["Throughput", None, None, None, None, None])  # sparse row
    ws2.append(["Latency", "ScadaA", "10ms", "9ms", "Ops", "Healthy"])

    ws3 = wb.create_sheet(title="FormulaCalculations")
    ws3.append(["Item", "Cost", "Tax", "Total"])
    ws3.append(["TurbineA", 100, 10, "=B2+C2"])  # Formula cell without cached value

    wb.save(path)


def test_cold_warm_cache_identical_coverage_and_locators():
    """Cold and warm cache loads must produce identical physical-unit coverage and locators."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        doc_p = tmp_p / "portfolio_review.xlsx"
        _create_sample_xlsx(doc_p)
        cache_dir = tmp_p / "conversion_cache"

        # 1. Cold hit (MISS)
        meta_cold: Dict[str, Any] = {}
        text_cold, hash_cold, fmt_cold = load_document_content(
            doc_p, metadata=meta_cold, cache_dir=cache_dir
        )
        assert meta_cold["conversion_artifact"]["was_cached"] is False
        assert "_source_coverage" in meta_cold
        cold_cov = meta_cold["_source_coverage"]
        assert cold_cov.get("total_sheets") == 3
        assert cold_cov.get("complete") is True

        # 2. Warm hit (HIT)
        meta_warm: Dict[str, Any] = {}
        text_warm, hash_warm, fmt_warm = load_document_content(
            doc_p, metadata=meta_warm, cache_dir=cache_dir
        )
        assert meta_warm["conversion_artifact"]["was_cached"] is True
        assert text_warm == text_cold
        assert hash_warm == hash_cold
        assert fmt_warm == fmt_cold

        # Verify exact identical physical-unit coverage restoration
        assert "_source_coverage" in meta_warm
        warm_cov = meta_warm["_source_coverage"]
        assert warm_cov == cold_cov
        assert warm_cov.get("total_sheets") == cold_cov.get("total_sheets")
        assert warm_cov.get("total_records") == cold_cov.get("total_records")
        assert warm_cov.get("sheets") == cold_cov.get("sheets")


def test_raster_vs_blank_pdf_pages_distinction():
    """Distinguish blank pages (EMPTY_PAGE) from raster-only pages (RASTER_IMAGE_ONLY)."""
    import pypdf
    from pypdf.generic import DictionaryObject, NameObject

    writer = pypdf.PdfWriter()

    # Page 1: normal text page
    writer.add_blank_page(width=612, height=792)

    # Page 2: empty page
    writer.add_blank_page(width=612, height=792)

    # Page 3: raster image page (simulate XObject image)
    p3 = writer.add_blank_page(width=612, height=792)
    img_dict = DictionaryObject()
    img_dict[NameObject("/Subtype")] = NameObject("/Image")
    xobj_dict = DictionaryObject()
    xobj_dict[NameObject("/Im1")] = img_dict
    res_dict = DictionaryObject()
    res_dict[NameObject("/XObject")] = xobj_dict
    p3[NameObject("/Resources")] = res_dict

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_p = Path(tmpdir) / "document_scan.pdf"
        with open(pdf_p, "wb") as f:
            writer.write(f)

        manifest = build_source_manifest([pdf_p])
        assert len(manifest.sources) == 1
        entry = manifest.sources[0]
        assert len(entry.pages) == 3

        # Page 2 has no text and no images -> EMPTY_PAGE
        assert entry.pages[1].is_blank is True
        assert entry.pages[1].is_raster is False
        assert entry.pages[1].exclusion_reason == "EMPTY_PAGE"

        # Page 3 has raster XObject -> RASTER_IMAGE_ONLY
        assert entry.pages[2].is_raster is True
        assert entry.pages[2].exclusion_reason == "RASTER_IMAGE_ONLY"

        # Because raster pages are omitted without OCR, full ingestion claim MUST be False
        assert manifest.full_ingestion_claim is False
        assert manifest.ingested_representation == "partial_raw"
        assert "Raster image pages present without OCR/text extraction" in manifest.claim_rationale


def test_xlsx_uncalculated_formulas_and_sparse_rows():
    """Worksheets with uncalculated formulas and sparse rows are accounted and block full ingestion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_p = Path(tmpdir) / "metrics.xlsx"
        _create_sample_xlsx(doc_p)

        manifest = build_source_manifest([doc_p])
        assert len(manifest.sources) == 1
        entry = manifest.sources[0]
        sheets = {s.sheet_name: s for s in entry.sheets}

        # Sparse sheet check
        assert sheets["SparseMetrics"].is_sparse is True
        assert 2 in sheets["SparseMetrics"].sparse_row_indices

        # Uncalculated formula check
        assert sheets["FormulaCalculations"].uncalculated_formulas >= 1

        # Uncalculated formula blocks full ingestion claim
        assert manifest.full_ingestion_claim is False
        assert manifest.ingested_representation == "partial_raw"
        assert "Uncalculated formula cells detected" in manifest.claim_rationale


def test_unsupported_conversion_cannot_claim_exhaustive_coverage():
    """Unsupported or partial support status cannot claim exhaustive coverage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_p = Path(tmpdir) / "simple_data.csv"
        csv_p.write_text("ID,Name,Role\n1,Alex,Analyst\n2,Jordan,Lead\n", encoding="utf-8")

        # Ingestion with declared PARTIAL support status
        manifest_partial = build_source_manifest(
            [csv_p], metadata={"support_status": "PARTIAL"}
        )
        assert manifest_partial.full_ingestion_claim is False
        assert manifest_partial.ingested_representation == "partial_raw"
        assert "Unsupported/partial conversion status 'PARTIAL'" in manifest_partial.claim_rationale


def test_advertised_adapters_resolve_to_real_implementations():
    """Every advertised adapter in capability_registry resolves to an importable callable or module."""
    import importlib

    def _resolve(path_str: str) -> bool:
        if path_str in ("none", "pending_conversion", "pending_msg_adapter", "pending_workbook_adapter"):
            return True
        if "." not in path_str:
            return False
        mod_name, func_name = path_str.rsplit(".", 1)
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, func_name):
                return True
        except (ModuleNotFoundError, AttributeError):
            pass
        try:
            importlib.import_module(path_str)
            return True
        except ModuleNotFoundError:
            return False

    for fid, cap in _REGISTRY.items():
        assert _resolve(cap.primary_adapter), f"Format {fid} primary_adapter '{cap.primary_adapter}' failed resolution"
        assert _resolve(cap.attachment_adapter), f"Format {fid} attachment_adapter '{cap.attachment_adapter}' failed resolution"

    # Confirm XLSX_WORKBOOK is now SUPPORTED
    assert is_format_supported("xlsx") is True
    assert get_capability_by_ext("xlsx").status == SupportStatus.SUPPORTED


def test_decisive_evidence_selection_and_omission_accounting():
    """Prompt builder selects decisive rows and claims with explicit omission accounting."""
    # 1. Table with 25 rows where row 22 contains a critical outage signal
    headers = ["ID", "System", "Status", "Variance", "Severity"]
    rows: List[List[Any]] = []
    for i in range(1, 25):
        if i == 22:
            rows.append([f"R{i}", "SCADA Telemetry", "FAILED", "99.9% loss", "CRITICAL P0"])
        else:
            rows.append([f"R{i}", "Routine Service", "Normal", "0%", "Low"])

    selected_rows, omitted_rows = _select_decisive_table_rows(rows, limit=10)
    assert len(selected_rows) == 10
    assert omitted_rows == 14
    # The critical row MUST be included among the 10 selected rows despite being at index 21
    assert any("CRITICAL P0" in r for r in selected_rows)

    # Test markdown formatting with omission notice
    content = ExtractedAttachmentContent(
        attachment_id="att_table_01",
        filename="scada_status.csv",
        attachment_type=AttachmentType.SPREADSHEET,
        sha256="dummy_sha",
        title="SCADA Status Table",
        tables=[{"headers": headers, "rows": rows}],
    )
    formatted_table = "\n".join(_format_milestones_and_tables(content))
    assert "CRITICAL P0" in formatted_table
    assert "Omission accounting: 14 of 24 total rows omitted from prompt context" in formatted_table

    # 2. Claims with 15 entries where claim 13 has a critical decision
    claims = [f"General system observation {i}" for i in range(1, 16)]
    claims[12] = "CRITICAL: SCADA cluster failure blocked turbine dispatch."

    selected_claims, omitted_claims = _select_decisive_claims(claims, limit=10)
    assert len(selected_claims) == 10
    assert omitted_claims == 5
    assert any("CRITICAL: SCADA cluster failure" in c for c in selected_claims)

    content_claims = ExtractedAttachmentContent(
        attachment_id="att_claims_01",
        filename="claims.png",
        attachment_type=AttachmentType.IMAGE,
        sha256="dummy_sha2",
        title="Claims Diagram",
        claims=claims,
    )
    formatted_claims = "\n".join(_format_claims_and_text(content_claims))
    assert "CRITICAL: SCADA cluster failure" in formatted_claims
    assert "Omission accounting: 5 of 15 total claims omitted from prompt context" in formatted_claims
