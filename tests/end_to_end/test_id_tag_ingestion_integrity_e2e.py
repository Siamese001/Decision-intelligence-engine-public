"""End-to-End and Adversarial Regression Test Suite for Project LENS ID-Tag Ingestion Integrity.

Verifies zero dropped ID-tag records (GEID/TEID stickers and physical nameplates),
resolving the defect identified in the 10-06-2026 debrief across boundary payloads,
malformed OCR noise, multi-threaded concurrency, and exact production regression cases.
"""
from __future__ import annotations

import concurrent.futures
from typing import List

import pytest

from dual_pipeline.lens_evaluation.id_tag_extractor import (
    IDTagExtractor,
    IDTagNormalizationResult,
    IDTagRecord,
)


# ==============================================================================
# 1. BOUNDARY PAYLOAD TESTS
# ==============================================================================

def test_boundary_payload_empty_and_whitespace() -> None:
    """Boundary test: empty strings, null characters, and pure whitespace."""
    records = [
        IDTagRecord("rec_empty_1", "WTG_01", ""),
        IDTagRecord("rec_empty_2", "WTG_01", "   "),
        IDTagRecord("rec_empty_3", "WTG_01", "\t\r\n"),
        IDTagRecord("rec_empty_4", "WTG_01", "\0"),
    ]
    results = IDTagExtractor.process_batch(records)
    assert len(results) == 4
    for res in results:
        assert res.is_valid is False
        assert res.error_code in ("ERR_EMPTY_PAYLOAD", "ERR_UNRECOGNIZED_PREFIX")
        assert res.canonical_tag is None
        assert res.confidence == 0.0


def test_boundary_payload_minimal_and_edge_lengths() -> None:
    """Boundary test: minimal length valid tag and pure numeric asset tag."""
    records = [
        IDTagRecord("rec_min_1", "WTG_02", "GEID-0"),
        IDTagRecord("rec_min_2", "WTG_02", "TEID-1"),
        IDTagRecord("rec_num_1", "WTG_02", "1234567"),  # Bare numeric asset tag
    ]
    results = IDTagExtractor.process_batch(records)
    assert len(results) == 3
    assert results[0].is_valid is True
    assert results[0].canonical_tag == "GEID-0"
    assert results[1].is_valid is True
    assert results[1].canonical_tag == "TEID-1"
    assert results[2].is_valid is True
    assert results[2].canonical_tag == "GEID-1234567"
    assert results[2].healed is True


# ==============================================================================
# 2. MALFORMED INPUT & OCR NOISE HEALING TESTS
# ==============================================================================

def test_malformed_ocr_prefix_substitutions() -> None:
    """Malformed test: OCR common optical misreads (1 for I, L for I, etc.)."""
    records = [
        IDTagRecord("rec_ocr_1", "WTG_03", "GE1D-WTG-0842"),
        IDTagRecord("rec_ocr_2", "WTG_03", "TE1D-INV-0012"),
        IDTagRecord("rec_ocr_3", "WTG_03", "GELD-XFMR-004"),
        IDTagRecord("rec_ocr_4", "WTG_03", "TELD-CBL-099"),
        IDTagRecord("rec_ocr_5", "WTG_03", "GE-ID-CAB-102"),
    ]
    results = IDTagExtractor.process_batch(records)
    assert len(results) == 5
    expected_tags = [
        "GEID-WTG-0842",
        "TEID-INV-0012",
        "GEID-XFMR-004",
        "TEID-CBL-099",
        "GEID-CAB-102",
    ]
    for res, expected in zip(results, expected_tags):
        assert res.is_valid is True, f"Failed for {res.raw_text}"
        assert res.canonical_tag == expected
        assert res.healed is True
        assert res.confidence >= 0.70


def test_malformed_delimiters_and_multiline_breaks() -> None:
    """Malformed test: colons, slashes, hashes, spaces, and line breaks."""
    records = [
        IDTagRecord("rec_delim_1", "WTG_04", "GEID: 88123-A"),
        IDTagRecord("rec_delim_2", "WTG_04", "TEID / WTG / 04 / B"),
        IDTagRecord("rec_delim_3", "WTG_04", "GEID#100234"),
        IDTagRecord("rec_delim_4", "WTG_04", "TEID-\n0412\r\nREV2"),
        IDTagRecord("rec_delim_5", "WTG_04", "geid   generator   554"),
    ]
    results = IDTagExtractor.process_batch(records)
    assert len(results) == 5
    assert results[0].canonical_tag == "GEID-88123-A"
    assert results[1].canonical_tag == "TEID-WTG-04-B"
    assert results[2].canonical_tag == "GEID-100234"
    assert results[3].canonical_tag == "TEID-0412-REV2"
    assert results[4].canonical_tag == "GEID-GENERATOR-554"


def test_malformed_incomplete_and_unrecoverable() -> None:
    """Malformed test: prefix without body or purely unrecoverable gibberish."""
    records = [
        IDTagRecord("rec_bad_1", "WTG_05", "GEID-"),
        IDTagRecord("rec_bad_2", "WTG_05", "TEID: : :"),
        IDTagRecord("rec_bad_3", "WTG_05", "XYZ-UNKNOWN-RANDOM"),
    ]
    results = IDTagExtractor.process_batch(records)
    assert len(results) == 3
    assert results[0].is_valid is False
    assert results[0].error_code in ("ERR_MISSING_BODY", "ERR_INVALID_BODY")
    assert results[1].is_valid is False
    assert results[1].error_code in ("ERR_MISSING_BODY", "ERR_INVALID_BODY")
    assert results[2].is_valid is False
    assert results[2].error_code == "ERR_UNRECOGNIZED_PREFIX"


# ==============================================================================
# 3. CONCURRENCY & RACE CONDITION TESTS
# ==============================================================================

def test_concurrent_multi_threaded_ingestion() -> None:
    """Concurrency test: multi-worker parallel parsing with zero dropped records."""
    num_workers = 8
    records_per_worker = 100
    all_records: List[IDTagRecord] = []

    for w in range(num_workers):
        for i in range(records_per_worker):
            rec_id = f"worker_{w}_rec_{i}"
            tag_text = f"GE1D-TURBINE-{w:02d}-{i:04d}"
            all_records.append(IDTagRecord(rec_id, f"WTG_{w:02d}", tag_text))

    assert len(all_records) == 800

    def _worker_fn(chunk: List[IDTagRecord]) -> List[IDTagNormalizationResult]:
        return IDTagExtractor.process_batch(chunk)

    chunk_size = records_per_worker
    chunks = [all_records[i:i + chunk_size] for i in range(0, len(all_records), chunk_size)]

    all_results: List[IDTagNormalizationResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(_worker_fn, chunk) for chunk in chunks]
        for f in concurrent.futures.as_completed(futures):
            all_results.extend(f.result())

    # Guarantee zero dropped records
    assert len(all_results) == 800
    for res in all_results:
        assert res.is_valid is True
        assert res.canonical_tag.startswith("GEID-TURBINE-")
        assert res.healed is True


# ==============================================================================
# 4. EXACT PRODUCTION REGRESSION (10-06-2026 DEBRIEF SCENARIO)
# ==============================================================================

def test_exact_production_regression_1006_debrief_cases() -> None:
    """Exact regression test pinning the 10-06-2026 debrief defect cases.

    Morgan Vance Krutilla and Jake Stark noted physical tags were falling through
    the OCR intake when nameplate stickers had trailing revisions or spaces.
    """
    production_cases = [
        IDTagRecord("prod_01", "WTG_04", "GEID-WTG-0842-A"),
        IDTagRecord("prod_02", "WTG_04", "TEID-INV-0012"),
        IDTagRecord("prod_03", "WTG_04", "TEID 9941"),
        IDTagRecord("prod_04", "WTG_04", "GEID: 88123"),
        IDTagRecord("prod_05", "WTG_04", "TEID-B-441-REV2"),
        IDTagRecord("prod_06", "WTG_04", "GEID#100234"),
        IDTagRecord("prod_07", "WTG_04", "GElD - SWITCH - 09"),
        IDTagRecord("prod_08", "WTG_04", "TE-ID_CONVERTER_44"),
    ]

    results = IDTagExtractor.process_batch(production_cases)

    # All 8 production cases must be successfully resolved with zero drops
    assert len(results) == 8
    for res in results:
        assert res.is_valid is True, f"Production regression drop on: {res.raw_text}"
        assert res.canonical_tag is not None
        assert res.tag_type in ("GEID", "TEID")

    # Verify specific canonical tag outputs
    assert results[0].canonical_tag == "GEID-WTG-0842-A"
    assert results[1].canonical_tag == "TEID-INV-0012"
    assert results[2].canonical_tag == "TEID-9941"
    assert results[3].canonical_tag == "GEID-88123"
    assert results[4].canonical_tag == "TEID-B-441-REV2"
    assert results[5].canonical_tag == "GEID-100234"
    assert results[6].canonical_tag == "GEID-SWITCH-09"
    assert results[7].canonical_tag == "TEID-CONVERTER-44"


def test_idempotent_reingestion_invariance() -> None:
    """Verify that re-processing identical records produces byte-for-byte identical outcomes."""
    sample = IDTagRecord("idem_01", "WTG_04", "GE1D: WTG-0842-A")
    res1 = IDTagExtractor.process_record(sample)
    res2 = IDTagExtractor.process_record(sample)
    assert res1.to_dict() == res2.to_dict()
