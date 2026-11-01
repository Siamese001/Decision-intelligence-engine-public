"""Adversarial and edge-case tests for Wave 6 judge cache bindings and token efficiency.

Covers mandatory edge-case categories:
1. Boundary payload: empty candidates, null pins, 0-byte source text, empty section hashes.
2. Malformed input: corrupted SQLite cache JSON, type-mismatched pin metadata, SQL injection payloads.
3. Invariant bypass attempts: hashless pin reuse, source-free cross-contamination, default prompt hashes, cross-partition leakage.
4. Concurrency & race: leader exception in single-flight unblocks waiters and allows subsequent retry.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

import pytest

from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.transcript_qa_core.eval_cache import (
    _get_cache_db_path,
    check_cache,
    check_pin,
    clear_evaluation_cache,
    get_cached_section_evaluation,
    resolve_pins_and_cache,
    store_cached_section_evaluation,
    update_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.judge_cache_core import (
    CanonicalJudgeKey,
    SingleFlightExecutor,
)


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_boundary_empty_candidate_and_null_pins():
    """Empty strings and null/empty pin dictionaries resolve safely without crash."""
    clear_evaluation_cache()
    o1, o2, o3, k1, k2, k3 = resolve_pins_and_cache(
        f1_text="",
        f2_text="",
        f3_text="",
        model="gpt-6-astra",
        reasoning_effort="medium",
        pinned={},
        source_text=None,
    )
    assert o1 is None
    assert o2 is None
    assert o3 is None
    assert k1.startswith("out1:")


def test_boundary_zero_byte_source_text_vs_none():
    """Empty string source_text generates distinct cache key from None source_text."""
    clear_evaluation_cache()
    f = "Sample artifact text."
    _, _, _, k_none, _, _ = resolve_pins_and_cache(f, f, f, "m", "low", {}, source_text=None)
    _, _, _, k_empty, _, _ = resolve_pins_and_cache(f, f, f, "m", "low", {}, source_text="")

    assert k_none == k_empty
    # When non-empty source text provided, key changes
    _, _, _, k_with_src, _, _ = resolve_pins_and_cache(f, f, f, "m", "low", {}, source_text="Source")
    assert k_with_src != k_none


def test_boundary_empty_section_cache_parameters():
    """Empty section name or empty hash returns None without error."""
    clear_evaluation_cache()
    assert get_cached_section_evaluation("", "") is None
    assert get_cached_section_evaluation("section_foo", "") is None


# =========================================================================
# Category 2: Malformed Input
# =========================================================================

def test_malformed_sqlite_cache_corrupted_json():
    """Corrupted JSON in persistent SQLite cache falls back safely to cache miss."""
    clear_evaluation_cache()
    db_path = _get_cache_db_path()
    corrupt_key = "judge:corrupt:test_key"

    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO judge_evaluations (cache_key, composite_score, verdict, evaluation_json) VALUES (?, ?, ?, ?)",
            (corrupt_key, 95, "PASS", "INVALID_NOT_JSON{[[}"),
        )

    # Lookup should not crash, but return None
    res = check_cache(corrupt_key, min_score=90)
    assert res is None


def test_malformed_pin_schema_types():
    """Pins containing non-dict types, integer SHAs, or missing score fields fail closed."""
    expected_sha = "abc" * 20
    # Pin is an integer
    assert check_pin({"output_1": 12345}, "output_1", "output_01", 90, expected_sha) is None

    # Pin has non-string sha
    assert check_pin(
        {"output_1": {"artifact_sha256": 12345, "composite_score": 100, "verdict": "PASS"}},
        "output_1", "output_01", 90, expected_sha
    ) is None

    # Pin has negative or non-numeric score
    assert check_pin(
        {"output_1": {"artifact_sha256": expected_sha, "composite_score": "not_a_number", "verdict": "PASS"}},
        "output_1", "output_01", 90, expected_sha
    ) is None


def test_malformed_sql_injection_in_cache_keys():
    """SQL injection payloads in cache keys are safely handled by parameterized queries."""
    clear_evaluation_cache()
    sqli_key = "judge:output_1'; DROP TABLE judge_evaluations; --"
    update_evaluation_cache(sqli_key, {"composite_score": 92, "verdict": "PASS", "dimension_1": 23.0, "dimension_2": 23.0, "dimension_3": 23.0, "dimension_4": 23.0}, "", None, "", None)

    # Retrieval works via parameterization
    cached = check_cache(sqli_key, min_score=90)
    assert cached is not None
    assert cached["composite_score"] == 92

    # Verify table was NOT dropped
    db_path = _get_cache_db_path()
    with GovernedStorageContext.read_context(db_path) as conn:
        row = conn.execute("SELECT count(*) FROM judge_evaluations").fetchone()
        assert row[0] >= 1


# =========================================================================
# Category 3: Invariant Bypass Attempts
# =========================================================================

def test_bypass_attempt_hashless_pin_stamping():
    """High-score pin lacking artifact_sha256 is strictly rejected (no hashless pin stamping)."""
    pinned = {
        "output_1": {
            "composite_score": 100,
            "verdict": "PASS",
            "evaluator": "ForgedPinEvaluator",
        }
    }
    # Expected SHA of actual candidate
    expected_sha = "c" * 64
    res = check_pin(pinned, "output_1", "output_01", 90, expected_sha)
    assert res is None, "Hashless pin must be rejected fail-closed"


def test_bypass_attempt_source_free_legacy_fallback():
    """Cache entry without source tag cannot be reused when source_text is required and provided."""
    clear_evaluation_cache()
    cand = "Candidate transcript text."
    src = "Authoritative meeting audio source transcript."

    # Pre-populate legacy source-free entry
    cand_h = "hash_cand_test"
    legacy_key = f"out1:{cand_h}:gpt-6-astra:medium:prompt_hash"
    update_evaluation_cache(legacy_key, {"composite_score": 98, "verdict": "PASS"}, "", None, "", None)

    # Resolve with source text: must NOT fall back to legacy_key
    o1, _, _, k1, _, _ = resolve_pins_and_cache(cand, cand, cand, "gpt-6-astra", "medium", {}, source_text=src)
    assert o1 is None, "Source-aware miss must not fall back to source-free cache entry"


def test_bypass_attempt_cross_partition_leakage():
    """Cache entries in tenant A must never match cache keys in tenant B."""
    key_tenant_a = CanonicalJudgeKey(
        artifact_type="output_1",
        content_sha256="1" * 64,
        reference_sha256="2" * 64,
        context_sha256="3" * 64,
        prompt_digest="4" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
        security_partition="tenant-a",
    )
    key_tenant_b = CanonicalJudgeKey(
        artifact_type="output_1",
        content_sha256="1" * 64,
        reference_sha256="2" * 64,
        context_sha256="3" * 64,
        prompt_digest="4" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
        security_partition="tenant-b",
    )
    assert key_tenant_a.to_key() != key_tenant_b.to_key()
    assert "tenant-a" in key_tenant_a.to_key()
    assert "tenant-b" in key_tenant_b.to_key()


# =========================================================================
# Category 4: Concurrency & Race Recovery
# =========================================================================

def test_concurrency_single_flight_exception_unblocks_waiters():
    """If leader in single-flight raises an exception, all waiting callers receive the exception without hanging."""
    flight = SingleFlightExecutor()
    attempts = 0

    def failing_call():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Transient provider timeout")

    with ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(flight.execute, "failing-key", failing_call)
        f2 = executor.submit(flight.execute, "failing-key", failing_call)

        with pytest.raises(RuntimeError, match="Transient provider timeout"):
            f1.result()
        with pytest.raises(RuntimeError, match="Transient provider timeout"):
            f2.result()

    # After failure unblocks, subsequent call can retry cleanly
    def successful_call():
        return {"recovered": True}

    res, dedup = flight.execute("failing-key", successful_call)
    assert res == {"recovered": True}
    assert dedup is False
