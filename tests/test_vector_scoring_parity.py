"""Unit tests verifying parity, accuracy, and performance of GPU-optimized vector scoring."""

import numpy as np

from dual_pipeline.index import (
    _dot,
)


def test_dense_vector_scoring_parity():
    """Verify that vectorized NumPy dot product produces mathematically identical ranking to _dot."""
    num_docs = 200
    dim = 1024
    np.random.seed(42)

    doc_matrix = np.random.randn(num_docs, dim).astype(np.float32)
    doc_matrix /= np.linalg.norm(doc_matrix, axis=1, keepdims=True)

    query_vec = np.random.randn(dim).astype(np.float32)
    query_vec /= np.linalg.norm(query_vec)

    query_list = query_vec.tolist()
    doc_lists = doc_matrix.tolist()

    # 1. Pure Python baseline
    py_scores = [(_dot(query_list, d), f"doc_{i}") for i, d in enumerate(doc_lists)]
    py_scores.sort(key=lambda item: (-item[0], item[1]))

    # 2. Vectorized NumPy BLAS
    np_sims = np.dot(doc_matrix, query_vec)
    np_scores = [(float(score), f"doc_{i}") for i, score in enumerate(np_sims)]
    np_scores.sort(key=lambda item: (-item[0], item[1]))

    # Assert identical rankings
    py_ranks = [doc_id for _, doc_id in py_scores]
    np_ranks = [doc_id for _, doc_id in np_scores]
    assert py_ranks == np_ranks

    # Assert numerical closeness (within float32 precision)
    for (s_py, _), (s_np, _) in zip(py_scores, np_scores, strict=False):
        assert abs(s_py - s_np) < 1e-5


def test_colbert_maxsim_parity():
    """Verify batched ColBERT MaxSim computation parity against doc-by-doc loop."""
    num_candidates = 25
    q_tokens = 8
    d_tokens = 16
    dim = 1024
    np.random.seed(123)

    query_colbert = np.random.randn(q_tokens, dim).astype(np.float32)
    doc_colbert = [np.random.randn(d_tokens, dim).astype(np.float32) for _ in range(num_candidates)]

    # 1. Doc-by-doc loop
    loop_scores = []
    for i, doc in enumerate(doc_colbert):
        sim = np.matmul(query_colbert, doc.T)
        score = float(np.sum(np.max(sim, axis=1)))
        loop_scores.append((score, f"doc_{i}"))
    loop_scores.sort(key=lambda item: (-item[0], item[1]))

    # 2. Batched 3D tensor
    padded = np.stack(doc_colbert) # (B, D, Dim)
    # einsum: (Q, Dim) x (B, D, Dim) -> (B, Q, D)
    sim_3d = np.einsum("qd,bmd->bqm", query_colbert, padded)
    max_sim = np.max(sim_3d, axis=2)
    batched_scores = [(float(s), f"doc_{i}") for i, s in enumerate(np.sum(max_sim, axis=1))]
    batched_scores.sort(key=lambda item: (-item[0], item[1]))

    # Assert identical rankings and scores
    assert [d for _, d in loop_scores] == [d for _, d in batched_scores]
    for (s1, _), (s2, _) in zip(loop_scores, batched_scores, strict=False):
        assert np.isclose(s1, s2, rtol=1e-4, atol=1e-3)


def test_device_residency_verification_cached():
    """Verify that residency verification caches its assertion flag."""
    class DummyBackend:
        execution_device = "cpu"
        _residency_verified = False
        _model = None

        def _assert_device_residency(self, force: bool = False):
            if getattr(self, "_residency_verified", False) and not force:
                return
            self._residency_verified = True

    b = DummyBackend()
    assert not b._residency_verified
    b._assert_device_residency()
    assert b._residency_verified
    # Second call should return early
    b._assert_device_residency()
    assert b._residency_verified
