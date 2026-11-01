"""End-to-end tests for BGE-M3 batching, length-sorted order invariance, and binary packing."""

from __future__ import annotations

import struct
from typing import Any, List

import numpy as np
import pytest

from dual_pipeline.grounding.colbert_maxsim_auditor import ColbertMaxSimAuditor
from dual_pipeline.index_core.backends import (
    clear_bge_m3_model_cache,
    clear_bge_manifest_cache,
)
from dual_pipeline.index_core.batching_optimizer import (
    decode_colbert_matrix,
    decode_dense_array,
    get_cached_manifest_validation,
    prepare_length_sorted_batches,
)
from dual_pipeline.index_core.constants import _pack, _unpack
from dual_pipeline.retrieval.bge_reranker_service import BgeRerankerService


class MockFlagModel:
    """Mock FlagModel to verify batched execution without loading 4GB weights."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_batch_size: int | None = None
        self.encoded_inputs: List[List[str]] = []

    def encode(
        self,
        texts: List[str],
        batch_size: int = 16,
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = True,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.last_batch_size = batch_size
        self.encoded_inputs.append(list(texts))
        n = len(texts)

        res: dict[str, Any] = {}
        if return_dense:
            # Deterministic mock 1024-dim dense vector based on text hash
            dense_list = []
            for t in texts:
                h = abs(hash(t)) % 10000 + 1
                vec = np.zeros(1024, dtype=np.float32)
                vec[0] = float(h) / 10000.0
                vec[1] = 0.5
                dense_list.append(vec)
            res["dense_vecs"] = dense_list

        if return_sparse:
            sparse_list = []
            for t in texts:
                sparse_list.append({"101": 0.8, "202": 0.4})
            res["lexical_weights"] = sparse_list

        if return_colbert_vecs:
            colbert_list = []
            for t in texts:
                colbert_mat = np.ones((8, 1024), dtype=np.float32) * 0.1
                colbert_list.append(colbert_mat)
            res["colbert_vecs"] = colbert_list

        return res


class MockBackend:
    """Mock backend implementing FlagEmbeddingBackend interface for auditor and batching tests."""

    def __init__(self) -> None:
        self._model = MockFlagModel()
        self.execution_device = "cpu"
        self.encode_many_called_with: List[List[str]] = []
        self.encode_many_colbert_called_with: List[List[str]] = []

    def _assert_device_residency(self) -> None:
        pass

    def _decode_encoded_result(self, result: dict[str, Any], index: int) -> dict[str, Any]:
        dense_raw = result["dense_vecs"][index]
        dense = decode_dense_array(np.asarray(dense_raw, dtype=np.float32))
        sparse = {str(k): float(v) for k, v in result["lexical_weights"][index].items()}
        colbert_raw = result["colbert_vecs"][index]
        colbert = decode_colbert_matrix(np.asarray(colbert_raw, dtype=np.float32))
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert_tokens": [f"token-{i}" for i in range(len(colbert))],
            "colbert": colbert,
        }

    def encode_many(self, texts: List[str], *, batch_size: int | None = None) -> List[dict[str, Any]]:
        self.encode_many_called_with.append(list(texts))
        sorted_unique, inverse_indices, _ = prepare_length_sorted_batches(texts)
        res = self._model.encode(
            sorted_unique,
            batch_size=batch_size or 16,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=True,
        )
        decoded = [self._decode_encoded_result(res, i) for i in range(len(sorted_unique))]
        return [decoded[idx] for idx in inverse_indices]

    def encode(self, text: str) -> dict[str, Any]:
        return self.encode_many([text])[0]

    def encode_many_colbert_only(self, texts: List[str], *, batch_size: int | None = None) -> List[List[List[float]]]:
        self.encode_many_colbert_called_with.append(list(texts))
        sorted_unique, inverse_indices, _ = prepare_length_sorted_batches(texts)
        res = self._model.encode(
            sorted_unique,
            batch_size=batch_size or 16,
            return_dense=False,
            return_sparse=False,
            return_colbert_vecs=True,
        )
        decoded = [decode_colbert_matrix(np.asarray(res["colbert_vecs"][i], dtype=np.float32)) for i in range(len(sorted_unique))]
        return [decoded[idx] for idx in inverse_indices]


def test_bit_identical_binary_packing() -> None:
    """Verify that NumPy-vectorized _pack and _unpack match struct.pack exactly."""
    rng = np.random.default_rng(42)
    floats = [float(x) for x in rng.standard_normal(1024, dtype=np.float32)]

    # struct.pack baseline
    expected_bytes = struct.pack(f"<{len(floats)}f", *floats)
    expected_unpacked = struct.unpack(f"<{len(floats)}f", expected_bytes)

    # Vectorized functions
    actual_bytes = _pack(floats)
    actual_unpacked = _unpack(actual_bytes)

    assert actual_bytes == expected_bytes, "Packed bytes differ from struct.pack baseline"
    assert actual_unpacked == expected_unpacked, "Unpacked tuple differs from struct.unpack baseline"


def test_length_sorted_deduplicated_batching_order_invariance() -> None:
    """Verify that length sorting and deduplication preserve caller ordering and values."""
    backend = MockBackend()
    raw_texts = [
        "short",
        "a very very very long passage of text for evaluation",
        "medium length proposition assertion",
        "short",  # duplicate of index 0
        "medium length proposition assertion",  # duplicate of index 2
        "tiny",
    ]

    results = backend.encode_many(raw_texts)

    assert len(results) == len(raw_texts)
    # Underlying model only saw 4 unique items
    assert len(backend._model.encoded_inputs[0]) == 4
    # The unique items passed to model were sorted by length: tiny (4), short (5), medium (35), a very (52)
    assert backend._model.encoded_inputs[0] == [
        "tiny",
        "short",
        "medium length proposition assertion",
        "a very very very long passage of text for evaluation",
    ]

    # Verify that caller order is restored
    assert results[0]["dense"] == results[3]["dense"]  # "short" == "short"
    assert results[2]["dense"] == results[4]["dense"]  # "medium..." == "medium..."
    assert results[0]["dense"] != results[1]["dense"]


def test_colbert_maxsim_auditor_single_batched_call() -> None:
    """Verify ColbertMaxSimAuditor uses single batched call instead of N+1 sequential calls."""
    backend = MockBackend()
    auditor = ColbertMaxSimAuditor(backend=backend)

    claim = "The system reduced latency by 45 percent."
    chunks = [
        "We observed a 45% reduction in overall embedding latency.",
        "The project timeline was extended by two weeks.",
        "Server CPU utilization remained within normal parameters.",
    ]

    res = auditor.audit_claim(claim, chunks)

    # Auditor should have called encode_many_colbert_only once with claim + 3 chunks (total 4)
    assert len(backend.encode_many_colbert_called_with) == 1
    assert backend.encode_many_colbert_called_with[0] == [claim] + chunks
    assert "best_score" in res
    assert "grounded" in res


def test_reranker_service_model_and_pair_caching() -> None:
    """Verify BgeRerankerService caches scores across repeated (query, doc) pairs."""
    svc = BgeRerankerService(model_name="mock-reranker")

    # Manually seed score in cache to test memoization without FlagReranker
    from dual_pipeline.retrieval.bge_reranker_service import (
        _PAIR_SCORE_CACHE,
        _RERANKER_CACHE_LOCK,
    )
    with _RERANKER_CACHE_LOCK:
        _PAIR_SCORE_CACHE[("mock-reranker", "query1", "doc1")] = 0.95

    scores = svc.predict([("query1", "doc1")])
    assert scores == [0.95]
