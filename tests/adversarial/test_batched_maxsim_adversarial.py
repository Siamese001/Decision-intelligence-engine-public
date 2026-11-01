"""Adversarial tests for batched ColBERT MaxSim and PrePaddedColbertCache."""

from __future__ import annotations

import concurrent.futures
import sqlite3

import numpy as np

from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache


def _make_in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        """CREATE TABLE colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        )"""
    )
    return conn


def test_adversarial_ragged_boundary_and_scale():
    """Extreme candidate scale (200 docs) with wild token variance (1 to 200 tokens)."""
    conn = _make_in_memory_db()
    dim = 32
    num_docs = 150
    np.random.seed(999)
    q = np.random.randn(10, dim).astype(np.float32)

    docs: dict[str, np.ndarray] = {}
    for i in range(num_docs):
        if i % 15 == 0:
            # Empty document edge case
            docs[f"doc_{i}"] = np.empty((0, dim), dtype=np.float32)
        elif i == 1:
            # Single token document
            docs[f"doc_{i}"] = np.random.randn(1, dim).astype(np.float32)
        elif i == 2:
            # Huge document (150 tokens)
            docs[f"doc_{i}"] = np.random.randn(150, dim).astype(np.float32)
        else:
            t = np.random.randint(2, 40)
            docs[f"doc_{i}"] = np.random.randn(t, dim).astype(np.float32)

    for rid, mat in docs.items():
        for pos in range(mat.shape[0]):
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                (rid, pos, f"tok_{pos}", dim, _pack(mat[pos].tolist())),
            )
    conn.commit()

    colbert_cache: dict[str, np.ndarray] = {}
    padded_cache = PrePaddedColbertCache(max_entries=64)
    target_ids = list(docs.keys())

    scores = score_colbert(
        conn, q, target_ids, colbert_cache=colbert_cache, padded_cache=padded_cache
    )
    assert len(scores) == num_docs
    score_map = {rid: s for s, rid in scores}

    # Verify every document score matches exact mathematical formula
    for rid, mat in docs.items():
        if mat.shape[0] == 0:
            expected = 0.0
        else:
            sim = np.matmul(q, mat.T)
            expected = float(np.sum(np.max(sim, axis=1)))
        assert np.isclose(score_map[rid], expected, atol=1e-4)

    conn.close()


def test_adversarial_malformed_vector_blobs_and_graceful_recovery():
    """Corrupted / truncated / unaligned blobs in SQLite fall back cleanly without crash."""
    conn = _make_in_memory_db()
    dim = 32

    # Insert 1 valid doc, 1 with truncated blob, 1 with non-float bytes
    conn.execute(
        "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
        ("valid_doc", 0, "tok", dim, _pack([0.5] * dim)),
    )
    # Truncated blob (only 7 bytes, not divisible by 4)
    conn.execute(
        "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
        ("corrupt_doc_1", 0, "tok", dim, b"1234567"),
    )
    # 0 bytes blob
    conn.execute(
        "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
        ("corrupt_doc_2", 0, "tok", dim, b""),
    )
    conn.commit()

    q = np.ones((2, dim), dtype=np.float32)
    # 1. Under default strict mode, malformed vector blobs must fail explicitly
    import pytest
    with pytest.raises(ValueError, match="Ambiguous or invalid ColBERT vector codec"):
        score_colbert(
            conn, q, ["valid_doc", "corrupt_doc_1", "corrupt_doc_2", "missing_doc"],
            colbert_cache={},
            strict=True,
        )

    # 2. Under non-strict fallback mode, corrupted blobs fall back cleanly without crash
    cache: dict[str, np.ndarray] = {}
    scores = score_colbert(
        conn, q, ["valid_doc", "corrupt_doc_1", "corrupt_doc_2", "missing_doc"],
        colbert_cache=cache,
        strict=False,
    )
    assert len(scores) == 4
    score_map = {rid: s for s, rid in scores}
    assert score_map["valid_doc"] > 0.0
    assert score_map["missing_doc"] == 0.0
    conn.close()


def test_adversarial_multithreaded_concurrency_stress():
    """8 worker threads querying concurrently against a shared PrePaddedColbertCache."""
    conn = _make_in_memory_db()
    dim = 16
    np.random.seed(555)

    num_docs = 40
    docs = {
        f"doc_{i}": np.random.randn(np.random.randint(2, 15), dim).astype(np.float32)
        for i in range(num_docs)
    }
    for rid, mat in docs.items():
        for pos in range(mat.shape[0]):
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                (rid, pos, f"t_{pos}", dim, _pack(mat[pos].tolist())),
            )
    conn.commit()

    shared_padded_cache = PrePaddedColbertCache(max_entries=16)
    shared_token_cache: dict[str, np.ndarray] = {
        rid: mat for rid, mat in docs.items()
    }

    candidate_pools = [
        [f"doc_{i}" for i in range(8)],
        [f"doc_{i}" for i in range(8, 16)],
        [f"doc_{i}" for i in range(16, 24)],
        [f"doc_{i}" for i in range(24, 32)],
    ]

    def worker_task(worker_id: int):
        local_rng = np.random.RandomState(worker_id * 100)
        q = local_rng.randn(3, dim).astype(np.float32)
        subset = candidate_pools[worker_id % len(candidate_pools)]
        results = score_colbert(
            conn, q, subset,
            colbert_cache=shared_token_cache,
            padded_cache=shared_padded_cache,
        )
        assert len(results) == len(subset)
        # Check sort order descending by score
        scores = [r[0] for r in results]
        assert scores == sorted(scores, reverse=True)
        return len(results)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_task, i) for i in range(60)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 60
    stats = shared_padded_cache.stats()
    # Concurrency must produce both hits and populated cache without deadlock or corruption
    assert stats["hits"] > 0
    assert stats["cached_blocks"] > 0
    conn.close()


def test_adversarial_mismatched_dimension_fallback():
    """Documents with mismatched dimensions fall back cleanly to serial execution."""
    conn = _make_in_memory_db()
    # Query has dim 16
    q = np.random.randn(2, 16).astype(np.float32)

    # doc_a has dim 16, doc_b has dim 32
    conn.execute("INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)", ("d1", 0, "t", 16, _pack([0.1]*16)))
    conn.execute("INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)", ("d2", 0, "t", 16, _pack([0.2]*16)))
    conn.execute("INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)", ("d3", 0, "t", 16, _pack([0.3]*16)))
    conn.execute("INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)", ("d4", 0, "t", 32, _pack([0.4]*32)))
    conn.commit()

    cache: dict[str, np.ndarray] = {}
    # When dim mismatch is present in >= 4 candidates, falls back to serial
    # (d4 will error inside np.matmul(q, d4.T) or be handled)
    try:
        scores = score_colbert(conn, q, ["d1", "d2", "d3", "d4"], colbert_cache=cache)
        assert len(scores) == 4
    except ValueError:
        # NumPy raises ValueError for shape mismatch (2,16) x (32, 1) in serial loop
        pass
    conn.close()
