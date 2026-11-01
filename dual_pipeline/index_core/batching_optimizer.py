"""Batching, sequence sorting, and model manifest cache helpers for BGE-M3."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np

from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, MAX_COLBERT_TOKENS

_MANIFEST_CACHE_LOCK = threading.RLock()
_MANIFEST_CACHE: Dict[Tuple[str, int, int, str], Dict[str, Any]] = {}


def get_cached_manifest_validation(
    model_root: Path,
    manifest_file: Path,
    model_revision: str,
    validator_func: Callable[[Path, Path, str], Dict[str, Any]],
) -> Dict[str, Any]:
    """Retrieve or compute validated model manifest cached by file state."""
    try:
        m_stat = manifest_file.stat()
        cache_key = (
            str(model_root.resolve()),
            int(m_stat.st_mtime_ns),
            int(m_stat.st_size),
            model_revision,
        )
    except Exception:
        return validator_func(model_root, manifest_file, model_revision)

    with _MANIFEST_CACHE_LOCK:
        if cache_key in _MANIFEST_CACHE:
            return dict(_MANIFEST_CACHE[cache_key])

    verified = validator_func(model_root, manifest_file, model_revision)
    with _MANIFEST_CACHE_LOCK:
        _MANIFEST_CACHE[cache_key] = verified
    return dict(verified)


def clear_bge_manifest_cache() -> None:
    """Clear in-memory verified manifest cache."""
    with _MANIFEST_CACHE_LOCK:
        _MANIFEST_CACHE.clear()


def prepare_length_sorted_batches(
    texts: Sequence[str],
) -> Tuple[List[str], List[int], List[int]]:
    """Deduplicate and sort texts by length ascending.

    Returns:
        sorted_unique_texts: Deduplicated unique strings sorted by length.
        inverse_indices: Mapping from original texts to indices in sorted_unique_texts.
        original_lengths: Character length of each original text.
    """
    if not texts:
        return [], [], []

    unique_texts: List[str] = []
    text_to_unique_idx: Dict[str, int] = {}
    inverse_to_unique: List[int] = []

    for text in texts:
        idx = text_to_unique_idx.get(text)
        if idx is None:
            idx = len(unique_texts)
            text_to_unique_idx[text] = idx
            unique_texts.append(text)
        inverse_to_unique.append(idx)

    # Sort unique texts by length
    sorted_order = sorted(range(len(unique_texts)), key=lambda i: len(unique_texts[i]))
    unique_to_sorted_idx = {orig_u_idx: sorted_pos for sorted_pos, orig_u_idx in enumerate(sorted_order)}

    sorted_unique_texts = [unique_texts[i] for i in sorted_order]
    final_inverse = [unique_to_sorted_idx[u_idx] for u_idx in inverse_to_unique]

    return sorted_unique_texts, final_inverse, [len(t) for t in texts]


def decode_dense_array(dense_raw: Any) -> List[float]:
    """Validate and convert 1D dense vector using vectorized NumPy operations."""
    dense_arr = np.asarray(dense_raw, dtype=np.float32)
    if dense_arr.ndim != 1 or dense_arr.shape[0] != DENSE_DIMENSIONS:
        raise RuntimeError("BGE-M3 returned an invalid dense vector; expected 1024 finite nonzero dimensions")
    if not np.isfinite(dense_arr).all() or not np.any(dense_arr != 0):
        raise RuntimeError("BGE-M3 returned an invalid dense vector; expected 1024 finite nonzero dimensions")
    return [float(x) for x in dense_arr.astype(float)]


def decode_colbert_matrix(colbert_raw: Any) -> List[List[float]]:
    """Validate and slice ColBERT token matrix using vectorized NumPy operations."""
    colbert_arr = np.asarray(colbert_raw, dtype=np.float32)
    sliced = colbert_arr[:MAX_COLBERT_TOKENS]
    if sliced.size == 0 or sliced.ndim != 2 or sliced.shape[1] == 0:
        raise RuntimeError("BGE-M3 returned missing, inconsistent, or non-finite ColBERT token vectors")
    if not np.isfinite(sliced).all():
        raise RuntimeError("BGE-M3 returned missing, inconsistent, or non-finite ColBERT token vectors")
    return [[float(val) for val in row] for row in sliced.astype(float)]
