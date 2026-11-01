"""Adversarial and edge-case tests for Wave 3 paired experiments and evaluation parity."""

from __future__ import annotations

import pytest

from dual_pipeline.retrieval_release_core.paired_experiments import (
    EvaluationMode,
    QueryJudgments,
    RetrievalConfig,
    evaluate_query_ranking,
    filter_active_candidates,
    rank_candidates_for_query,
    validate_holdout_protection,
)


def test_adversarial_boundary_empty_candidates_and_zero_positives():
    # Category: Boundary payload
    empty_ranking = rank_candidates_for_query([], RetrievalConfig.HYBRID_FULL, EvaluationMode.FIXED_POOL_RERANK)
    assert empty_ranking == []

    gt_zero_positives = QueryJudgments(
        query_id="q_zero",
        query_text="Unanswerable query?",
        topic_id="top0",
        phrasing_type="keyword",
        judgments={"c1": 0, "c2": 0},
        relevant_ids=set(),
        is_answerable=False,
    )
    cand_recall, metrics_at_k = evaluate_query_ranking(["c1", "c2"], gt_zero_positives, cutoffs=(5, 10))
    # Unanswerable query returns None for positive candidate recall to prevent inflation
    assert cand_recall is None
    assert metrics_at_k[5]["ndcg"] == 0.0
    assert metrics_at_k[5]["mrr"] == 0.0


def test_adversarial_boundary_k_larger_than_retrieved():
    # Category: Boundary payload
    gt = QueryJudgments(
        query_id="q_small",
        query_text="Text",
        topic_id="top1",
        phrasing_type="exec",
        judgments={"c1": 2},
        relevant_ids={"c1"},
        is_answerable=True,
    )
    # Only 1 candidate retrieved, cutoff k=20
    cand_recall, metrics_at_k = evaluate_query_ranking(["c1"], gt, cutoffs=(20,))
    assert cand_recall == 1.0
    assert metrics_at_k[20]["mrr"] == 1.0
    assert metrics_at_k[20]["recall"] == 1.0
    # 0 unjudged because c1 is in judgments
    assert metrics_at_k[20]["unjudged"] == 0.0


def test_adversarial_malformed_input_corrupt_scores_and_missing_keys():
    # Category: Malformed input
    corrupt_candidates = [
        {"record_id": "c1", "dense_score": None, "bm25_rank": "invalid"},
        {"record_id": "c2", "dense_score": float("nan"), "bm25_rank": None},
        {"record_id": "c3", "dense_score": 0.42, "dense_rank": 1},
    ]
    # DENSE_ONLY should cleanly rank c3 first and treat c1/c2 as bottom without crashing
    ranking = rank_candidates_for_query(corrupt_candidates, RetrievalConfig.DENSE_ONLY, EvaluationMode.FIXED_POOL_RERANK)
    assert ranking[0] == "c3"


def test_adversarial_malformed_input_empty_record_id_fails_closed():
    # Category: Malformed input
    invalid_candidates = [
        {"record_id": "", "dense_score": 0.9, "dense_rank": 1},
    ]
    with pytest.raises(ValueError, match="Invalid record_id"):
        rank_candidates_for_query(invalid_candidates, RetrievalConfig.DENSE_ONLY, EvaluationMode.FIXED_POOL_RERANK)


def test_adversarial_bypass_duplicate_record_id_in_ranking_fails_closed():
    # Category: Invariant bypass attempt
    duplicate_candidates = [
        {"record_id": "c1", "dense_score": 0.9, "dense_rank": 1},
        {"record_id": "c1", "dense_score": 0.8, "dense_rank": 2},
    ]
    with pytest.raises(ValueError, match="Duplicate record IDs detected"):
        rank_candidates_for_query(duplicate_candidates, RetrievalConfig.DENSE_ONLY, EvaluationMode.FIXED_POOL_RERANK)


def test_adversarial_bypass_disabled_engine_cannot_invent_candidates():
    # Category: Invariant bypass attempt
    candidates = [
        {"record_id": "c_dense_only", "dense_rank": 1, "bm25_rank": None, "sparse_rank": None, "colbert_rank": None},
        {"record_id": "c_bm25_only", "dense_rank": None, "bm25_rank": 1, "sparse_rank": None, "colbert_rank": None},
    ]
    # Under BM25_ONLY in regenerated mode, c_dense_only MUST NOT be admitted
    admitted_bm25 = filter_active_candidates(candidates, RetrievalConfig.BM25_ONLY, EvaluationMode.REGENERATED_CANDIDATES)
    assert "c_dense_only" not in [c["record_id"] for c in admitted_bm25]
    assert [c["record_id"] for c in admitted_bm25] == ["c_bm25_only"]


def test_adversarial_bypass_holdout_leakage_detection():
    # Category: Invariant bypass attempt
    partitions = {
        "query_partitions": {
            "dev": ["dev_01", "dev_02"],
            "final_holdout": ["holdout_p0_critical"],
        }
    }
    with pytest.raises(ValueError, match="HOLDOUT LEAKAGE VIOLATION"):
        validate_holdout_protection(["dev_01", "holdout_p0_critical"], partitions)


def test_adversarial_exact_production_regression_bm25_deficit_on_paraphrase():
    # Category: Exact production regression
    # Paraphrase query where dense and colbert capture semantic intent but BM25 lexical match fails
    paraphrase_candidates = [
        {"record_id": "c_semantic_answer", "dense_score": 0.88, "dense_rank": 1, "sparse_rank": 1, "colbert_rank": 1, "bm25_rank": None},
        {"record_id": "c_lexical_distractor", "dense_score": 0.12, "dense_rank": 15, "sparse_rank": 18, "colbert_rank": 20, "bm25_rank": 1},
    ]
    gt = QueryJudgments(
        query_id="q_paraphrase",
        query_text="What are operational bottlenecks?",
        topic_id="top1",
        phrasing_type="executive",
        judgments={"c_semantic_answer": 2, "c_lexical_distractor": 0},
        relevant_ids={"c_semantic_answer"},
        is_answerable=True,
    )
    # BM25 in regenerated mode fails to retrieve semantic answer (cand recall = 0)
    bm25_ranking = rank_candidates_for_query(
        paraphrase_candidates, RetrievalConfig.BM25_ONLY, EvaluationMode.REGENERATED_CANDIDATES
    )
    bm25_recall, bm25_metrics = evaluate_query_ranking(bm25_ranking, gt, cutoffs=(5,))
    assert bm25_recall == 0.0
    assert bm25_metrics[5]["mrr"] == 0.0

    # HYBRID_FULL in regenerated mode successfully admits semantic answer and places it rank 1
    hybrid_ranking = rank_candidates_for_query(
        paraphrase_candidates, RetrievalConfig.HYBRID_FULL, EvaluationMode.REGENERATED_CANDIDATES
    )
    hybrid_recall, hybrid_metrics = evaluate_query_ranking(hybrid_ranking, gt, cutoffs=(5,))
    assert hybrid_recall == 1.0
    assert hybrid_metrics[5]["mrr"] == 1.0


def test_adversarial_concurrency_race_condition_hybrid_retriever_threads():
    # Category: Concurrency & race
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import numpy as np
    from dual_pipeline.core import read_json
    from dual_pipeline.index_core.retriever import HybridRetriever
    from dual_pipeline.index_core.sidecar import validate_sidecar

    repo_root = Path(__file__).resolve().parents[2]
    runtime_root = repo_root / ".local" / "dual_pipeline"
    if not runtime_root.exists():
        runtime_root = repo_root.parent.parent / ".local" / "dual_pipeline"

    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        pytest.skip("Active pair not found for live concurrency test")

    active_pair = read_json(active_pair_path)
    sidecar_path = runtime_root / active_pair["sidecar_path"]
    if not sidecar_path.is_file():
        pytest.skip("Active sidecar not found for live concurrency test")

    meta = validate_sidecar(sidecar_path)["metadata"]

    class MockAlignedBackend:
        def __init__(self, metadata: dict):
            self.model_id = metadata.get("model_id")
            self.model_manifest_digest = metadata.get("model_manifest_digest")
            self.model_revision = metadata.get("model_revision")
            self.backend_id = metadata.get("embedding_backend")
            self.assurance_tier = metadata.get("assurance_tier", "development")
            self.production_qualified = metadata.get("production_qualified", "false") == "true"
            self.dim = int(metadata.get("dense_dim", 1024))
            self.encode_calls = 0

        def encode(self, query: str):
            self.encode_calls += 1
            dense_vec = [1.0 / np.sqrt(self.dim)] * self.dim
            return {
                "dense": dense_vec,
                "sparse": {"101": 0.5, "202": 0.3},
                "colbert": [[1.0 / np.sqrt(self.dim)] * self.dim for _ in range(4)],
            }

    backend = MockAlignedBackend(meta)
    retriever = HybridRetriever(sidecar_path, backend)

    queries = [f"test_query_{i % 8}" for i in range(32)]

    def _run_lane(q: str):
        return retriever.retrieve_lane(
            query=q,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=5,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_run_lane, q) for q in queries]
        results = [f.result() for f in futures]

    assert len(results) == 32
    assert all(len(r["results"]) > 0 for r in results)
    assert len(retriever._query_encoding_cache) == 8
    assert backend.encode_calls == 8
    assert len(retriever._colbert_token_cache) > 0

