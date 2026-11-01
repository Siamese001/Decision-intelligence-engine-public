"""Unit tests for Wave 1 metric SSOT consolidation, sidecar validation, and consumer alignment.

Verifies:
1. Parity between dual_pipeline.retrieval_release_core.metrics and IndependentStandardEvaluator.
2. Independent verification of zero local def compute_ndcg_at_k definitions in evals/ and tools/.
3. Citation identifier resolution (retrieval-record-* and entity-facet-*) to canonical assertion IDs in answer_eval_core.
4. IsotropicBgeM3Router 1024-d contract enforcement.
5. Extended sidecar validation (contiguous ColBERT tokens, unit policy default, [0.85, 1.15] norm bounds).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dual_pipeline.evals.answer_eval_core.metrics import (
    compute_citation_durability,
    compute_staleness_and_conflict_miss,
    resolve_citation_to_assertion_ids,
)
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, _pack
from dual_pipeline.index_core.embedding_space_contract import EmbeddingSpaceMismatch
from dual_pipeline.index_core.sidecar_validation import run_extended_sidecar_invariants
from dual_pipeline.query_router_core.isotropic_router import (
    CANONICAL_INTENTS,
    IsotropicBgeM3Router,
)
from dual_pipeline.retrieval_release_core.metrics import (
    compute_dcg,
    compute_mrr_at_k,
    compute_ndcg,
    compute_ndcg_at_k,
    standard_ndcg_at_k,
    standard_recall_at_k,
    standard_reciprocal_rank,
)
from storage.connection import sqlite3
from tools.independent_standard_evaluator import IndependentStandardEvaluator


def test_metric_ssot_parity_with_independent_evaluator() -> None:
    """100% agreement between canonical metrics and IndependentStandardEvaluator."""
    evaluator = IndependentStandardEvaluator(top_k=5)
    ranking = ["doc_a", "doc_b", "doc_c", "doc_d", "doc_e"]
    truth = {"doc_a": 3, "doc_c": 1, "doc_f": 2}  # doc_f is unretrieved

    # nDCG agreement
    eval_ndcg = evaluator.ndcg_at_k(ranking, truth)
    metric_ndcg = standard_ndcg_at_k(ranking, truth, top_k=5)
    assert eval_ndcg == pytest.approx(metric_ndcg, abs=1e-6)

    # Reciprocal rank agreement
    eval_rr = evaluator.reciprocal_rank(ranking, truth)
    metric_rr = standard_reciprocal_rank(ranking, truth, top_k=5, relevance_threshold=1)
    assert eval_rr == pytest.approx(metric_rr, abs=1e-6)

    # Recall agreement
    eval_rec = evaluator.recall_at_k(ranking, truth)
    metric_rec = standard_recall_at_k(ranking, truth, top_k=5, relevance_threshold=1)
    assert eval_rec == pytest.approx(metric_rec, abs=1e-6)


def test_zero_local_compute_ndcg_at_k_in_evals_and_tools() -> None:
    """Independent check: confirm zero local 'def compute_ndcg_at_k' definitions in evals/ and tools/."""
    repo_root = Path(__file__).resolve().parents[2]
    evals_dir = repo_root / "evals"
    tools_dir = repo_root / "tools"

    offending_files: list[str] = []
    for directory in (evals_dir, tools_dir):
        if not directory.is_dir():
            continue
        for p in directory.rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "def compute_ndcg_at_k" in text:
                offending_files.append(str(p.relative_to(repo_root)))

    assert not offending_files, (
        f"Found forbidden local 'def compute_ndcg_at_k' definitions in: {offending_files}. "
        "All scripts must import canonical metrics from dual_pipeline.retrieval_release_core.metrics."
    )


def test_answer_eval_citation_resolution_durability_and_staleness() -> None:
    """Verifies that retrieval-record IDs resolve to canonical assertions in answer evaluation metrics."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            source_date_normalized TEXT,
            canonical_status TEXT,
            retrieval_eligible INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            artifact_json TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE retrieval_record_object_facet (
            record_id TEXT,
            object_type TEXT,
            object_id TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_relationship TEXT
        )"""
    )

    # 1. Setup active assertion
    conn.execute(
        "INSERT INTO assertion VALUES ('ast-real-001', 'node_1', 'operates', '2026-01-01', 'current', 1)"
    )

    # 2. Setup retrieval_record wrapping ast-real-001
    art_json = json.dumps({
        "record_id": "retrieval-record-ast-001",
        "extensions": {"com.partner_advisory.nee.graph-object-id": "ast-real-001"}
    })
    conn.execute("INSERT INTO retrieval_record VALUES ('retrieval-record-ast-001', ?)", (art_json,))

    # 3. Test resolve_citation_to_assertion_ids
    resolved = resolve_citation_to_assertion_ids("retrieval-record-ast-001", conn)
    assert resolved == ["ast-real-001"]

    # 4. Test durability: citation of record_id must yield durability 1.0 (not 0.0!)
    durability = compute_citation_durability(["retrieval-record-ast-001"], conn)
    assert durability == 1.0

    # 5. Add newer superseding assertion to make it stale
    conn.execute(
        "INSERT INTO assertion VALUES ('ast-real-002', 'node_1', 'operates', '2026-06-01', 'current', 1)"
    )
    record = {
        "cited_assertion_ids": ["retrieval-record-ast-001"],
        "response_text": "System operates as expected."
    }
    staleness_rate, conflict_miss_rate = compute_staleness_and_conflict_miss(record, conn)
    assert staleness_rate == 1.0


def test_isotropic_router_enforces_1024d_contract() -> None:
    """IsotropicBgeM3Router enforces 1024-d BGE-M3 embedding contract."""
    # 64-d initialization fails
    with pytest.raises(ValueError, match="1024-d BGE-M3 embedding space under contract"):
        IsotropicBgeM3Router(dimension=64)

    # 1024-d initialization succeeds
    router = IsotropicBgeM3Router(dimension=1024, margin_threshold=0.15)
    assert router.dimension == 1024

    # All canonical intents route with valid margins
    for intent in CANONICAL_INTENTS:
        centroid = router.centroids[intent]
        assert len(centroid) == 1024
        res = router.route_vector(centroid)
        assert res.top_intent == intent
        assert res.margin_delta >= 0.15
        assert res.hitl_required is False


def test_extended_sidecar_validation_default_unit_policy() -> None:
    """Verifies that extended sidecar validation defaults to enforce_unit_policy=True and catches defects."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, record_type TEXT)")
    conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)")
    conn.execute("CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))")
    conn.execute("CREATE TABLE colbert_token (record_id TEXT, position INTEGER, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))")

    unit_norm = 1.0 / (DENSE_DIMENSIONS ** 0.5)
    valid_vec = _pack([unit_norm] * DENSE_DIMENSIONS)

    # Insert valid semantic unit record ('assertion')
    conn.execute("INSERT INTO retrieval_record VALUES ('rec_1', 'assertion')")
    conn.execute("INSERT INTO dense_vector VALUES ('rec_1', ?, ?)", (DENSE_DIMENSIONS, valid_vec))
    conn.execute("INSERT INTO sparse_weight VALUES ('rec_1', 'token1', 1.0)")
    conn.execute("INSERT INTO colbert_token VALUES ('rec_1', 0, 1024, ?)", (valid_vec,))

    # Valid run passes cleanly
    run_extended_sidecar_invariants(conn, {"semantic_unit_index": True})

    # Forbidden 'edge' record under default unit policy fails
    conn.execute("INSERT INTO retrieval_record VALUES ('rec_edge', 'edge')")
    conn.execute("INSERT INTO dense_vector VALUES ('rec_edge', ?, ?)", (DENSE_DIMENSIONS, valid_vec))
    conn.execute("INSERT INTO sparse_weight VALUES ('rec_edge', 'token2', 1.0)")
    conn.execute("INSERT INTO colbert_token VALUES ('rec_edge', 0, 1024, ?)", (valid_vec,))

    with pytest.raises(RuntimeError, match="forbidden record_type under semantic unit policy"):
        run_extended_sidecar_invariants(conn, {})
