"""
tests/test_hr360_source_preflight.py

Acceptance tests for Wave 2: Source Preflight & Complete Provenance Accounting.
Verifies:
1. Missing files and 0-byte corrupt files fail before extraction provider call.
2. Missing dependencies (openpyxl, pypdf) fail early.
3. Malformed audience and oversized title fail preflight.
4. All worksheets in multi-sheet workbooks are inventoried and extracted (no active-sheet-only omission).
5. Empty sheets receive explicit EXCLUDED disposition with reason.
6. PDF pages receive complete page-by-page coverage accounting.
7. Composed summaries are identified and linked to raw source provenance.
8. Full ingestion cannot be claimed from attachment counts alone.
9. Conversion cache reuses matching content+converter digests and invalidates upon byte change.
"""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.conversion_cache import (
    compute_conversion_cache_key,
    resolve_conversion_with_cache,
)
from dual_pipeline.document_pipeline_core.preflight import (
    validate_intake_preflight,
)
from dual_pipeline.document_pipeline_core.source_manifest import (
    build_source_manifest,
    save_source_manifest,
)
from dual_pipeline.exceptions import SourceIntegrityError

HRIT_DIR = Path("documents/hrit").resolve()
PDF_SOURCE = HRIT_DIR / "HR360_PRF_Current_vs_Future_State.pdf"
POSITION_XLSX = HRIT_DIR / "Position Integration Field Mapping - Create & Update.xlsx"
REQ_XLSX = HRIT_DIR / "Requisition_Technical_Field_Mapping 4.xlsx"


@pytest.fixture
def sandbox_dir():
    d = tempfile.mkdtemp(prefix="test_preflight_sandbox_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_missing_source_file_fails_preflight():
    """Missing file paths must fail immediately before any extraction call."""
    nonexistent = Path("/tmp/nonexistent_source_doc_xyz123.xlsx")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        validate_intake_preflight(nonexistent)


def test_zero_byte_source_file_fails_preflight(sandbox_dir: Path):
    """Zero-byte source files must fail closed with SourceIntegrityError."""
    empty_file = sandbox_dir / "empty_workbook.xlsx"
    empty_file.write_bytes(b"")
    with pytest.raises((SourceIntegrityError, ValueError), match="0 bytes"):
        validate_intake_preflight(empty_file)


def test_corrupt_spreadsheet_fails_preflight(sandbox_dir: Path):
    """Non-zip files masquerading as xlsx must fail preflight archive checks."""
    corrupt_file = sandbox_dir / "corrupt.xlsx"
    corrupt_file.write_bytes(b"This is not a zip file or valid xlsx content.")
    with pytest.raises(ValueError, match="not a valid zip archive"):
        validate_intake_preflight(corrupt_file)


def test_missing_dependency_fails_preflight(monkeypatch):
    """Missing parser dependency (e.g. openpyxl) must fail preflight with clear message."""
    import sys

    # Simulate missing openpyxl
    monkeypatch.setitem(sys.modules, "openpyxl", None)

    with pytest.raises(RuntimeError, match="openpyxl"):
        validate_intake_preflight(POSITION_XLSX)


def test_malformed_audience_metadata_fails_preflight():
    """Malformed audience metadata must fail preflight before provider call."""
    invalid_meta = {"audiences": ["Invalid Audience With Spaces!!"]}
    with pytest.raises(ValueError, match="Audience serialization validation failed"):
        validate_intake_preflight(PDF_SOURCE, invalid_meta)


def test_oversized_title_fails_preflight():
    """Oversized title exceeding envelope limits must fail preflight."""
    oversized_meta = {"title": "A" * 300}
    with pytest.raises(ValueError, match="Evidence field source.title contains"):
        validate_intake_preflight(PDF_SOURCE, oversized_meta)


def test_all_worksheet_inventory_and_extraction():
    """
    Verify all 4 worksheets in Position workbook are inventoried and extracted.
    The old defect loaded only the active sheet ('Read Me'), omitting 3 sheets.
    """
    meta: dict = {}
    text, _, fmt = load_document_content(POSITION_XLSX, metadata=meta)

    assert fmt == "xlsx"
    coverage = meta.get("_source_coverage", {})
    assert coverage.get("total_sheets") == 4
    assert coverage.get("loaded_sheets") == 4
    assert coverage.get("all_sheets_loaded") is True

    # Check that all sheet titles appear in extracted Markdown
    assert "Sheet: Read Me" in text
    assert "Sheet: Field Map (all 87)" in text
    assert "Sheet: Create Payload" in text
    assert "Sheet: Business Rules" in text

    # Check total records >= 165
    assert coverage.get("total_records", 0) >= 165


def test_empty_sheet_explicit_exclusion_accounting(sandbox_dir: Path):
    """Sheets with zero non-empty rows must have EXCLUDED disposition with EMPTY_SHEET reason."""
    wb = openpyxl.Workbook()
    ws_data = wb.active
    ws_data.title = "DataSheet"
    ws_data.append(["Col1", "Col2"])
    ws_data.append(["Val1", "Val2"])

    ws_empty = wb.create_sheet(title="EmptySheet")
    # leave ws_empty completely blank

    test_file = sandbox_dir / "two_sheets.xlsx"
    wb.save(test_file)

    manifest = build_source_manifest([test_file])
    assert manifest.total_sources == 1
    src = manifest.sources[0]
    assert len(src.sheets) == 2

    sheet_by_name = {s.sheet_name: s for s in src.sheets}
    assert sheet_by_name["DataSheet"].disposition == "INCLUDED"
    assert sheet_by_name["DataSheet"].nonempty_rows == 2

    assert sheet_by_name["EmptySheet"].disposition == "EXCLUDED"
    assert sheet_by_name["EmptySheet"].exclusion_reason == "EMPTY_SHEET"
    assert sheet_by_name["EmptySheet"].nonempty_rows == 0


def test_pdf_page_coverage_accounting():
    """Verify PDF pages receive page-by-page coverage accounting."""
    manifest = build_source_manifest([PDF_SOURCE])
    assert manifest.total_sources == 1
    src = manifest.sources[0]
    assert src.format == "pdf"
    assert len(src.pages) == 4

    for p in src.pages:
        assert p.char_count > 0
        assert p.disposition == "INCLUDED"
        assert p.exclusion_reason is None


def test_summary_vs_source_provenance():
    """Composed summaries must be distinguished from raw sources and linked by provenance."""
    sources = [PDF_SOURCE, POSITION_XLSX, REQ_XLSX]
    manifest = build_source_manifest(sources)

    # All three are raw sources
    assert all(not s.is_summary for s in manifest.sources)
    assert manifest.full_ingestion_claim is True
    assert manifest.ingested_representation == "raw_sources"


def test_attachment_counts_alone_cannot_claim_full_ingestion(sandbox_dir: Path):
    """
    Ingesting only a summary document while raw workbooks exist must NOT claim
    full ingestion based on attachment counts alone.
    """
    summary_file = sandbox_dir / "composed_email_recap.md"
    summary_file.write_text(
        "From: architect@example.com\nSubject: Summary\nAttachments: pos.xlsx, req.xlsx\n\nHigh-level recap.",
        encoding="utf-8",
    )

    manifest = build_source_manifest(
        [summary_file, POSITION_XLSX],
        metadata={"ingest_mode": "summary_only"},
    )

    # In summary_only mode, full_ingestion_claim must be FALSE
    assert manifest.full_ingestion_claim is False
    assert manifest.ingested_representation == "composed_summary"
    assert "Only composed summary" in manifest.claim_rationale


def test_conversion_cache_reuse_and_invalidation(sandbox_dir: Path):
    """
    Conversion cache must reuse matching content+converter digests and
    invalidate immediately when workbook content bytes change.
    """
    cache_dir = sandbox_dir / "conv_cache"
    cache_dir.mkdir()

    # Step 1: Initial conversion -> cache MISS
    wb1 = openpyxl.Workbook()
    ws1 = wb1.active
    ws1.title = "Main"
    ws1.append(["Key", "Value"])
    ws1.append(["A", "1"])
    f1 = sandbox_dir / "wb.xlsx"
    wb1.save(f1)
    raw1 = f1.read_bytes()

    text1, key1, was_cached1, rec1 = resolve_conversion_with_cache(
        source_p=f1,
        raw_bytes=raw1,
        format_name="xlsx",
        loader_fn=lambda: "### Sheet: Main\n| Row | Key | Value |\n| R1 | A | 1 |",
        cache_dir=cache_dir,
        converter_version="v1.0",
    )
    assert was_cached1 is False
    assert "A | 1" in text1

    # Step 2: Second call with identical content -> cache HIT
    text2, key2, was_cached2, rec2 = resolve_conversion_with_cache(
        source_p=f1,
        raw_bytes=raw1,
        format_name="xlsx",
        loader_fn=lambda: pytest.fail("Loader should not be called on cache HIT"),
        cache_dir=cache_dir,
        converter_version="v1.0",
    )
    assert was_cached2 is True
    assert key2 == key1
    assert text2 == text1

    # Step 3: Modify workbook content -> cache MISS (different key)
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "Main"
    ws2.append(["Key", "Value"])
    ws2.append(["B", "2"])
    wb2.save(f1)
    raw2 = f1.read_bytes()

    text3, key3, was_cached3, rec3 = resolve_conversion_with_cache(
        source_p=f1,
        raw_bytes=raw2,
        format_name="xlsx",
        loader_fn=lambda: "### Sheet: Main\n| Row | Key | Value |\n| R1 | B | 2 |",
        cache_dir=cache_dir,
        converter_version="v1.0",
    )
    assert was_cached3 is False
    assert key3 != key1
    assert "B | 2" in text3

    # Step 4: Same content but changed converter version -> cache MISS
    text4, key4, was_cached4, rec4 = resolve_conversion_with_cache(
        source_p=f1,
        raw_bytes=raw2,
        format_name="xlsx",
        loader_fn=lambda: "### Sheet: Main\n| Row | Key | Value |\n| R1 | B | 2 | (v2.0)",
        cache_dir=cache_dir,
        converter_version="v2.0",
    )
    assert was_cached4 is False
    assert key4 != key3
    assert "(v2.0)" in text4
