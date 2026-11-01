"""Wave 2 verification tests for preliminary union quota, query-normalized ColBERT MaxSim, and entity prefix matching."""
from __future__ import annotations

import sqlite3
import numpy as np

from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.filters import _exact_entity_title_matches
from dual_pipeline.index_core.fusion import (
    compute_candidate_union,
    fuse_hybrid_ranks,
    renormalize_active_rrf_weights,
    score_colbert,
)
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache


def _make_colbert_db(records: dict[str, list[list[float]]]) -> sqlite3.Connection:
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


def test_preliminary_union_quota_protects_dense_top_hits_under_bm25_skew() -> None:
    """Top dense semantic paraphrases must be admitted into candidate union even when 100 BM25 records dominate."""
    bm25_ranks = {f"bm25-only-{i:03d}": i for i in range(1, 101)}
    dense_ranks = {
        "dense-paraphrase-top1": 1,
        "dense-paraphrase-top2": 2,
        **{f"bm25-only-{i:03d}": i + 10 for i in range(1, 90)},
    }
    sparse_ranks = {
        "sparse-synonym-top1": 1,
        **{f"bm25-only-{i:03d}": i + 5 for i in range(1, 90)},
    }
    preliminary_ranks = {
        "dense": dense_ranks,
        "sparse": sparse_ranks,
        "bm25": bm25_ranks,
    }
    # Even under extreme BM25 skew (bm25: 0.70, dense: 0.05, sparse: 0.15) and union_limit=40:
    skewed_weights = {"dense": 0.05, "sparse": 0.15, "bm25": 0.70, "colbert": 0.10}
    admitted = compute_candidate_union(
        preliminary_ranks,
        skewed_weights,
        rrf_k=60,
        union_limit=40,
    )
    assert len(admitted) == 40
    assert "dense-paraphrase-top1" in admitted
    assert "dense-paraphrase-top2" in admitted
    assert "sparse-synonym-top1" in admitted


def test_colbert_query_length_normalization_and_canonical_tile_caching() -> None:
    """Query-normalized ColBERT MaxSim bounds scores in [-1, 1] and canonicalizes tile cache keys."""
    dim = 8
    unit_vec = [1.0] + [0.0] * (dim - 1)
    docs = {f"doc_{i}": [unit_vec, unit_vec] for i in range(6)}
    conn = _make_colbert_db(docs)
    colbert_cache: dict[str, np.ndarray] = {}
    padded_cache = PrePaddedColbertCache(max_entries=8)

    # 5 unit query tokens matching unit doc tokens -> raw MaxSim = 5.0, normalized = 1.0
    query_5_tokens = [unit_vec for _ in range(5)]
    scores_perm1 = score_colbert(
        conn,
        query_5_tokens,
        ["doc_5", "doc_1", "doc_3", "doc_0", "doc_4", "doc_2"],
        colbert_cache=colbert_cache,
        padded_cache=padded_cache,
        normalize_by_query_length=True,
    )
    assert padded_cache.stats()["cached_blocks"] == 1
    for val, _ in scores_perm1:
        assert -1.0 - 1e-6 <= val <= 1.0 + 1e-6
        assert abs(val - 1.0) < 1e-5

    # Permuted candidate order must hit the exact same PrePaddedColbertCache entry
    scores_perm2 = score_colbert(
        conn,
        query_5_tokens,
        ["doc_0", "doc_1", "doc_2", "doc_3", "doc_4", "doc_5"],
        colbert_cache=colbert_cache,
        padded_cache=padded_cache,
        normalize_by_query_length=True,
    )
    assert padded_cache.stats()["hits"] == 1
    assert scores_perm1 == scores_perm2
    conn.close()


def test_renormalize_active_rrf_weights_when_bm25_empty() -> None:
    """When bm25_ranks is empty (e.g. entity_facet or paraphrase), active channels renormalize to sum=1.0."""
    weights = {"dense": 0.05, "sparse": 0.15, "bm25": 0.70, "colbert": 0.10}
    renorm = renormalize_active_rrf_weights(weights, ["dense", "sparse", "colbert"])
    assert renorm["bm25"] == 0.0
    assert abs(sum(renorm.values()) - 1.0) < 1e-9
    assert abs(renorm["colbert"] - (0.10 / 0.30)) < 1e-9

    # Verify fuse_hybrid_ranks uses renormalized weights when bm25 is empty
    authorized = {
        "rec-a": {"record_id": "rec-a", "record_type": "entity_facet"},
    }
    results, _ = fuse_hybrid_ranks(
        admitted=["rec-a"],
        ranks={"dense": {"rec-a": 1}, "sparse": {"rec-a": 1}, "bm25": {}, "colbert": {"rec-a": 1}},
        weights=weights,
        rrf_k=60,
        injected_record_ids=[],
        exact_entity_title_record_ids=[],
        authorized_records=authorized,
        lane="approved",
        top_k=5,
        component_scores={"dense": {"rec-a": 0.9}, "sparse": {"rec-a": 0.8}, "colbert": {"rec-a": 0.95}},
    )
    # With renormalized weights summing to 1.0, rank-1 across all active engines yields 1.0 / 61
    assert abs(results[0]["fused_score"] - (1.0 / 61.0)) < 1e-9


def test_exact_entity_title_matches_natural_language_without_prefix() -> None:
    """Natural-language queries naming an entity match its entity_facet record without 'Entity facet:' prefix."""
    records = {
        "entity-facet-ai-hub": {
            "record_type": "entity_facet",
            "title": "Entity facet: AI Hub Foundations / Catalyst",
        },
        "entity-facet-access": {
            "record_type": "entity_facet",
            "title": "Entity facet: Governed internal-data access",
        },
    }
    # Natural-language query without 'Entity facet:' prefix
    matched = _exact_entity_title_matches(
        "What are the current milestones for AI Hub Foundations / Catalyst?",
        records,
    )
    assert matched == ["entity-facet-ai-hub"]
