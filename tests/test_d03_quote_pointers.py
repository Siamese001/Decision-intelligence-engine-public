"""Unit tests for D03 position-pointer quote extraction and resolution."""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    locate_quote_pointer,
    resolve_evidence_pointers,
    resolve_quote_from_pointer,
)


def test_resolve_quote_from_pointer_valid():
    """Verify verbatim extraction from valid character pointers."""
    source = "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    # List format
    assert resolve_quote_from_pointer(source, [11, 14]) == "ABC"
    # Tuple format
    assert resolve_quote_from_pointer(source, (11, 14)) == "ABC"
    # Dict format
    assert resolve_quote_from_pointer(source, {"start_idx": 11, "end_idx": 14}) == "ABC"


def test_resolve_quote_from_pointer_invalid_bounds():
    """Verify invalid pointer bounds raise ValueError."""
    source = "Hello world from the architecture review"

    # Negative start index
    with pytest.raises(ValueError, match="start_idx cannot be negative"):
        resolve_quote_from_pointer(source, [-1, 5])

    # End index out of range
    with pytest.raises(ValueError, match="exceeds source text length"):
        resolve_quote_from_pointer(source, [0, len(source) + 10])

    # Reversed bounds
    with pytest.raises(ValueError, match="start_idx .* >= end_idx"):
        resolve_quote_from_pointer(source, [15, 10])

    # Equal bounds (zero length)
    with pytest.raises(ValueError, match="start_idx .* >= end_idx"):
        resolve_quote_from_pointer(source, [5, 5])

    # Whitespace only
    source_with_spaces = "Hello        world"
    with pytest.raises(ValueError, match="contains only whitespace"):
        resolve_quote_from_pointer(source_with_spaces, [5, 12])


def test_resolve_quote_from_pointer_type_errors():
    """Verify non-integer or malformed pointers raise ValueError."""
    source = "Valid source text"

    with pytest.raises(ValueError, match="Invalid pointer format"):
        resolve_quote_from_pointer(source, "not-a-pointer")

    with pytest.raises(ValueError, match="Pointer indices must be integers"):
        resolve_quote_from_pointer(source, [1.5, 4.2])

    with pytest.raises(ValueError, match="source_text must be a string"):
        resolve_quote_from_pointer(None, [0, 5])


def test_locate_quote_pointer():
    """Verify locate_quote_pointer finds exact and case-insensitive offsets."""
    source = "The quick brown fox jumps over the lazy dog"

    assert locate_quote_pointer(source, "brown fox") == [10, 19]
    assert locate_quote_pointer(source, "BROWN FOX") == [10, 19]
    assert locate_quote_pointer(source, "nonexistent elephant") is None
    assert locate_quote_pointer("", "quote") is None
    assert locate_quote_pointer(source, "") is None


def test_resolve_evidence_pointers_lifecycle():
    """Verify resolve_evidence_pointers transforms pointers to verbatim text in evidence_index."""
    source = "Alex Brennan confirmed the L2-D03 quote pointer architecture is operational."

    projection_data = {
        "schema_version": "5.7",
        "evidence_index": {
            "ev-01": {
                "kind": "technical",
                "quote_pointer": [0, 12],  # "Alex Brennan"
                "speaker": "Alex Brennan",
            },
            "ev-02": {
                "kind": "decision",
                "text": "quote pointer architecture is operational",
                "speaker": "Alex Brennan",
            },
        },
    }

    resolved = resolve_evidence_pointers(projection_data, source)

    # ev-01 should have resolved exact_text
    ev1 = resolved["evidence_index"]["ev-01"]
    assert ev1["exact_text"] == "Alex Brennan"
    assert ev1["text"] == "Alex Brennan"
    assert ev1["quote_pointer"] == [0, 12]

    # ev-02 should have reverse-bound quote_pointer
    ev2 = resolved["evidence_index"]["ev-02"]
    assert ev2["quote_pointer"] is not None
    assert ev2["exact_text"] == "quote pointer architecture is operational"
