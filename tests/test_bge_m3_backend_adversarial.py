"""Adversarial and boundary tests for BGE-M3 backend, length sorting, and manifest caching."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dual_pipeline.index_core.batching_optimizer import (
    clear_bge_manifest_cache,
    decode_colbert_matrix,
    decode_dense_array,
    get_cached_manifest_validation,
    prepare_length_sorted_batches,
)


def test_prepare_length_sorted_batches_empty_and_single() -> None:
    """Boundary test: empty inputs, single item, and all duplicates."""
    # Empty
    texts, inv, lengths = prepare_length_sorted_batches([])
    assert texts == []
    assert inv == []
    assert lengths == []

    # Single item
    texts, inv, lengths = prepare_length_sorted_batches(["hello"])
    assert texts == ["hello"]
    assert inv == [0]
    assert lengths == [5]

    # All duplicate items
    texts, inv, lengths = prepare_length_sorted_batches(["same", "same", "same", "same"])
    assert texts == ["same"]
    assert inv == [0, 0, 0, 0]
    assert lengths == [4, 4, 4, 4]


def test_prepare_length_sorted_batches_extreme_length_skew() -> None:
    """Adversarial test: 0-char empty string mixed with massive string and symbols."""
    raw = [
        "a" * 1500,
        "",
        "🌟" * 10,
        "a" * 1500,  # duplicate massive
        "",  # duplicate empty
        "b" * 50,
    ]
    unique, inv, lengths = prepare_length_sorted_batches(raw)
    assert len(unique) == 4
    # Length order: "" (0), 🌟*10 (10), b*50 (50), a*1500 (1500)
    assert unique[0] == ""
    assert unique[-1] == "a" * 1500
    # Reconstructed list matches original exactly
    reconstructed = [unique[idx] for idx in inv]
    assert reconstructed == raw


def test_decode_dense_array_adversarial_validation() -> None:
    """Boundary & adversarial test: NaN, Inf, all zeros, and shape mismatches fail closed."""
    # Invalid dimension
    with pytest.raises(RuntimeError, match="expected 1024"):
        decode_dense_array(np.ones(512, dtype=np.float32))

    # All zeros
    with pytest.raises(RuntimeError, match="expected 1024"):
        decode_dense_array(np.zeros(1024, dtype=np.float32))

    # Contains NaN
    nan_arr = np.ones(1024, dtype=np.float32)
    nan_arr[42] = float("nan")
    with pytest.raises(RuntimeError, match="expected 1024"):
        decode_dense_array(nan_arr)

    # Contains Inf
    inf_arr = np.ones(1024, dtype=np.float32)
    inf_arr[100] = float("inf")
    with pytest.raises(RuntimeError, match="expected 1024"):
        decode_dense_array(inf_arr)

    # Valid array passes
    valid = np.ones(1024, dtype=np.float32) * 0.5
    decoded = decode_dense_array(valid)
    assert len(decoded) == 1024
    assert decoded[0] == 0.5


def test_decode_colbert_matrix_adversarial_validation() -> None:
    """Boundary & adversarial test: empty matrix, non-finite values, and truncation."""
    # Empty
    with pytest.raises(RuntimeError, match="ColBERT token vectors"):
        decode_colbert_matrix(np.zeros((0, 1024), dtype=np.float32))

    # Contains NaN
    nan_mat = np.ones((5, 1024), dtype=np.float32)
    nan_mat[2, 3] = float("nan")
    with pytest.raises(RuntimeError, match="ColBERT token vectors"):
        decode_colbert_matrix(nan_mat)

    # Slicing: more than MAX_COLBERT_TOKENS (32) must be safely truncated to 32
    large_mat = np.ones((64, 1024), dtype=np.float32)
    decoded = decode_colbert_matrix(large_mat)
    assert len(decoded) == 32
    assert len(decoded[0]) == 1024


def test_cached_manifest_validation_invalidation_on_mutation() -> None:
    """Verify that get_cached_manifest_validation re-executes when file mtime or size changes."""
    clear_bge_manifest_cache()

    with tempfile.TemporaryDirectory() as tmp_dir:
        model_root = Path(tmp_dir) / "model"
        model_root.mkdir()
        manifest_file = Path(tmp_dir) / "manifest.json"
        manifest_file.write_text('{"v": 1}', encoding="utf-8")

        calls = 0

        def dummy_validator(root: Path, manifest: Path, rev: str) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"status": "PASS", "call": calls, "manifest_digest": "dummy"}

        # First call: validator executes
        res1 = get_cached_manifest_validation(model_root, manifest_file, "rev1", dummy_validator)
        assert res1["call"] == 1
        assert calls == 1

        # Second call on identical file: cached!
        res2 = get_cached_manifest_validation(model_root, manifest_file, "rev1", dummy_validator)
        assert res2["call"] == 1
        assert calls == 1

        # Modify manifest file: cache invalidates and calls validator again
        manifest_file.write_text('{"v": 2, "extra": "data"}', encoding="utf-8")
        res3 = get_cached_manifest_validation(model_root, manifest_file, "rev1", dummy_validator)
        assert res3["call"] == 2
        assert calls == 2
