"""Unit tests for batched vectorized ColBERT MaxSim and PrePaddedColbertCache."""

from __future__ import annotations

import sqlite3

import numpy as np

from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache


def _make_in_memory_colbert_db(records: dict[str, list[list[float]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        )"""
    )
    for record_id, token_vectors in records.items():
        for pos, vec in enumerate(token_vectors):
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                (record_id, pos, f"tok_{pos}", len(vec), _pack(vec)),
            )
    conn.commit()
    return conn


def test_empty_target_ids_and_none_query():
    conn = sqlite3.connect(":memory:")
    assert score_colbert(conn, None, []) == []
    query = [[0.1, 0.2]]
    assert score_colbert(conn, query, []) == []

    # None query with targets produces zero scores sorted by id
    res = score_colbert(conn, None, ["doc_b", "doc_a"])
    assert res == [(0.0, "doc_a"), (0.0, "doc_b")]
    conn.close()


def test_small_candidate_pool_fallback():
    # Candidates < 4 falls back to serial execution
    dim = 16
    np.random.seed(42)
    q = np.random.randn(4, dim).astype(np.float32)
    doc_a = np.random.randn(6, dim).astype(np.float32)
    doc_b = np.random.randn(8, dim).astype(np.float32)

    conn = _make_in_memory_colbert_db({
        "doc_a": doc_a.tolist(),
        "doc_b": doc_b.tolist(),
    })
    cache: dict[str, np.ndarray] = {}
    scores = score_colbert(conn, q, ["doc_a", "doc_b"], colbert_cache=cache)

    assert len(scores) == 2
    # Verify manual score calculation
    expected_a = float(np.sum(np.max(np.matmul(q, doc_a.T), axis=1)))
    expected_b = float(np.sum(np.max(np.matmul(q, doc_b.T), axis=1)))
    score_dict = {rid: s for s, rid in scores}
    assert np.isclose(score_dict["doc_a"], expected_a, atol=1e-4)
    assert np.isclose(score_dict["doc_b"], expected_b, atol=1e-4)
    conn.close()


def test_batched_maxsim_uniform_tokens():
    dim = 32
    num_candidates = 8
    np.random.seed(101)
    q = np.random.randn(6, dim).astype(np.float32)
    docs = {
        f"doc_{i}": np.random.randn(10, dim).astype(np.float32)
        for i in range(num_candidates)
    }

    conn = _make_in_memory_colbert_db({k: v.tolist() for k, v in docs.items()})
    cache: dict[str, np.ndarray] = {}
    target_ids = list(docs.keys())

    scores = score_colbert(conn, q, target_ids, colbert_cache=cache)
    assert len(scores) == num_candidates

    # Verify each score against serial calculation
    for score_val, rid in scores:
        expected = float(np.sum(np.max(np.matmul(q, docs[rid].T), axis=1)))
        assert np.isclose(score_val, expected, atol=1e-4)
    conn.close()


def test_batched_maxsim_ragged_tokens_and_empty_doc():
    dim = 32
    num_candidates = 12
    np.random.seed(202)
    q = np.random.randn(5, dim).astype(np.float32)

    docs: dict[str, np.ndarray] = {}
    for i in range(num_candidates):
        if i == 3:
            docs[f"doc_{i}"] = np.empty((0, dim), dtype=np.float32)
        else:
            token_count = np.random.randint(1, 25)
            docs[f"doc_{i}"] = np.random.randn(token_count, dim).astype(np.float32)

    conn = _make_in_memory_colbert_db({
        k: v.tolist() for k, v in docs.items() if v.shape[0] > 0
    })
    cache: dict[str, np.ndarray] = {}
    target_ids = list(docs.keys())

    scores = score_colbert(conn, q, target_ids, colbert_cache=cache)
    score_dict = {rid: s for s, rid in scores}

    # Empty document must score exactly 0.0
    assert score_dict["doc_3"] == 0.0

    # Every ragged document score must strictly match serial MaxSim
    for rid, doc_mat in docs.items():
        if doc_mat.shape[0] == 0:
            expected = 0.0
        else:
            expected = float(np.sum(np.max(np.matmul(q, doc_mat.T), axis=1)))
        assert np.isclose(score_dict[rid], expected, atol=1e-4)
    conn.close()


def test_pre_padded_cache_hit_and_eviction():
    cache = PrePaddedColbertCache(max_entries=3)
    assert cache.stats() == {"hits": 0, "misses": 0, "cached_blocks": 0}

    k1 = ("a", "b", "c", "d")
    s1 = np.ones((4, 5, 8), dtype=np.float32)
    l1 = np.array([5, 5, 5, 5], dtype=np.int32)
    cache.set(k1, s1, l1, None)

    assert cache.get(k1) is not None
    assert cache.stats()["hits"] == 1
    assert cache.get(("nonexistent",)) is None
    assert cache.stats()["misses"] == 1

    # Fill to capacity and trigger FIFO eviction
    k2 = ("e", "f", "g", "h")
    k3 = ("i", "j", "k", "l")
    k4 = ("m", "n", "o", "p")
    cache.set(k2, s1, l1, None)
    cache.set(k3, s1, l1, None)
    cache.set(k4, s1, l1, None)

    assert cache.stats()["cached_blocks"] == 3
    # Oldest key k1 must have been evicted
    assert cache.get(k1) is None
    assert cache.get(k4) is not None

    cache.clear()
    assert cache.stats() == {"hits": 0, "misses": 0, "cached_blocks": 0}


def test_score_colbert_uses_pre_padded_cache():
    dim = 16
    np.random.seed(303)
    q = np.random.randn(4, dim).astype(np.float32)
    docs = {f"doc_{i}": np.random.randn(8, dim).astype(np.float32) for i in range(6)}

    conn = _make_in_memory_colbert_db({k: v.tolist() for k, v in docs.items()})
    colbert_cache: dict[str, np.ndarray] = {}
    padded_cache = PrePaddedColbertCache(max_entries=10)
    target_ids = list(docs.keys())

    # Call 1: Miss on padded_cache, sets block
    scores_1 = score_colbert(
        conn, q, target_ids, colbert_cache=colbert_cache, padded_cache=padded_cache
    )
    assert padded_cache.stats()["cached_blocks"] == 1

    # Call 2: Hit on padded_cache
    scores_2 = score_colbert(
        conn, q, target_ids, colbert_cache=colbert_cache, padded_cache=padded_cache
    )
    assert padded_cache.stats()["hits"] == 1
    assert scores_1 == scores_2
    conn.close()
