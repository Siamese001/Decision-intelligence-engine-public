"""Adversarial and boundary stress tests for D03 position-pointer quote architecture.

Covers:
1. Boundary payloads (single character, entire document, empty source text).
2. Malformed input (nulls, non-integers, nested structures, malformed dicts).
3. Invariant bypass attempts (out-of-bounds offsets, inverted bounds, negative offsets).
4. Concurrency races (multi-threaded concurrent pointer resolutions).
5. Rollback and fault recovery (error handling on corrupted evidence indices).
"""
from __future__ import annotations

import concurrent.futures

import pytest

from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    resolve_evidence_pointers,
    resolve_quote_from_pointer,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - exact boundary limits and full-text spans."""
    source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # Single character at start
    assert resolve_quote_from_pointer(source, [0, 1]) == "A"

    # Single character at end
    assert resolve_quote_from_pointer(source, [25, 26]) == "Z"

    # Entire document span
    assert resolve_quote_from_pointer(source, [0, 26]) == source

    # Empty source text
    with pytest.raises(ValueError, match="exceeds source text length|start_idx .* >= end_idx"):
        resolve_quote_from_pointer("", [0, 1])


def test_adversarial_malformed_input():
    """Category 2: Malformed input - non-integer indices, corrupted dict structures."""
    source = "Sample source text for malformed testing"

    # Floating point numbers
    with pytest.raises(ValueError, match="Pointer indices must be integers"):
        resolve_quote_from_pointer(source, [0.0, 5.0])

    # String numbers
    with pytest.raises(ValueError, match="Pointer indices must be integers"):
        resolve_quote_from_pointer(source, ["0", "5"])

    # Dict missing end_idx
    with pytest.raises(ValueError, match="Invalid pointer format"):
        resolve_quote_from_pointer(source, {"start_idx": 0})

    # Null pointer
    with pytest.raises(ValueError, match="Invalid pointer format"):
        resolve_quote_from_pointer(source, None)


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Invariant bypass attempts - out-of-bounds, negative, and reversed bounds."""
    source = "Secure source text"

    # Negative index
    with pytest.raises(ValueError, match="start_idx cannot be negative"):
        resolve_quote_from_pointer(source, [-5, 10])

    # Out of range end index
    with pytest.raises(ValueError, match="exceeds source text length"):
        resolve_quote_from_pointer(source, [0, 999999])

    # Inverted bounds
    with pytest.raises(ValueError, match="Invalid slice bounds"):
        resolve_quote_from_pointer(source, [15, 5])


def test_adversarial_concurrency_race():
    """Category 4: Concurrency races - multi-threaded concurrent pointer extraction."""
    source = "Thread safe verification text " * 100

    def run_worker(idx: int):
        start = (idx * 5) % 100
        end = start + 15
        return resolve_quote_from_pointer(source, [start, end])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_worker, i) for i in range(40)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 40
    for r in results:
        assert len(r) > 0


def test_adversarial_fault_recovery():
    """Category 5: Rollback and fault recovery on corrupted evidence dictionaries."""
    source = "Valid source text"
    corrupted_projection = {
        "evidence_index": {
            "ev-valid": {"kind": "technical", "quote_pointer": [0, 5]},
            "ev-corrupted": {"kind": "decision", "quote_pointer": [9999, 10000]},  # Invalid bounds
        }
    }

    # Should raise ValueError and identify the invalid pointer
    with pytest.raises(ValueError, match="exceeds source text length"):
        resolve_evidence_pointers(corrupted_projection, source)
