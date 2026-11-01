"""Embedding space contract enforcement: model revision, dimensions, norms, and cache binding."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..core import digest
from .constants import CANONICAL_EMBEDDING_MODEL_ID, DENSE_DIMENSIONS

logger = logging.getLogger("dual_pipeline.index_core.embedding_space_contract")


class EmbeddingSpaceMismatch(ValueError):
    """Raised when query or vector embedding space deviates from the active model contract."""
    pass


def assert_query_vector_contract(
    query_vector: np.ndarray | list[float] | tuple[float, ...],
    expected_dim: int = DENSE_DIMENSIONS,
) -> np.ndarray:
    """Verifies that a query vector matches required dimension, norm, and finiteness constraints."""
    if isinstance(query_vector, (list, tuple)):
        vec = np.asarray(query_vector, dtype=np.float32)
    elif isinstance(query_vector, np.ndarray):
        vec = query_vector.astype(np.float32)
    else:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: unsupported query vector type {type(query_vector)}"
        )

    if vec.ndim != 1 or vec.shape[0] != expected_dim:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: query vector dimension {vec.shape[0]} does not match expected {expected_dim}"
        )

    if np.isnan(vec).any() or np.isinf(vec).any():
        raise EmbeddingSpaceMismatch(
            "EMBEDDING_SPACE_MISMATCH: query vector contains NaN or Inf values"
        )

    norm = float(np.linalg.norm(vec))
    if norm < 1e-4:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: query vector has zero or near-zero norm: {norm}"
        )

    if norm < 0.85 or norm > 1.15:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: query vector violates L2-normalization contract: norm={norm:.4f}"
        )

    return vec


def validate_embedding_space_contract(
    *,
    backend: Any,
    sidecar_metadata: dict[str, Any],
    query_vector: np.ndarray | list[float] | None = None,
) -> None:
    """Validates full compatibility between backend, query representation, and sidecar metadata."""
    expected_backend = getattr(backend, "backend_id", None)
    expected_model = getattr(backend, "model_id", None)
    expected_rev = getattr(backend, "model_revision", None)
    expected_digest = getattr(backend, "model_manifest_digest", None)

    sidecar_backend = sidecar_metadata.get("embedding_backend")
    sidecar_model = sidecar_metadata.get("model_id")
    sidecar_rev = sidecar_metadata.get("model_revision")
    sidecar_digest = sidecar_metadata.get("model_manifest_digest")
    sidecar_dim = int(sidecar_metadata.get("dense_dimensions", DENSE_DIMENSIONS))

    if sidecar_dim != DENSE_DIMENSIONS:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: sidecar dense dimensions {sidecar_dim} != {DENSE_DIMENSIONS}"
        )

    if expected_backend and sidecar_backend and expected_backend != sidecar_backend:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: backend mismatch {expected_backend} vs {sidecar_backend}"
        )

    if expected_model and sidecar_model and expected_model != sidecar_model:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: model_id mismatch {expected_model} vs {sidecar_model}"
        )

    if expected_rev and sidecar_rev and expected_rev != sidecar_rev:
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: model_revision mismatch {expected_rev} vs {sidecar_rev}"
        )

    if (
        expected_digest
        and sidecar_digest
        and expected_digest != "not-applicable"
        and sidecar_digest != "not-applicable"
        and expected_digest != sidecar_digest
    ):
        raise EmbeddingSpaceMismatch(
            f"EMBEDDING_SPACE_MISMATCH: model_manifest_digest mismatch {expected_digest} vs {sidecar_digest}"
        )

    if query_vector is not None:
        assert_query_vector_contract(query_vector, expected_dim=sidecar_dim)


def build_query_cache_key(backend: Any, query: str) -> str:
    """Computes deterministic cache key bound to query text and exact backend model revision."""
    key_payload = [
        getattr(backend, "backend_id", "bge-m3"),
        getattr(backend, "model_id", CANONICAL_EMBEDDING_MODEL_ID),
        getattr(backend, "model_revision", ""),
        getattr(backend, "model_manifest_digest", "") or "",
        getattr(backend, "encoding_fingerprint_digest", "") or "",
        query,
    ]
    return digest(key_payload)

