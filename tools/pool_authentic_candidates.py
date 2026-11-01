"""Tool to pool authentic candidates across BM25, Dense, Sparse, and ColBERT for Wave 3."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import numpy as np


def _tokenize(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r"\b\w+\b", text)]


def _build_bm25_index(corpus: List[Dict[str, Any]]) -> Tuple[List[List[str]], List[int], float, Dict[str, float]]:
    corpus_tokens = [_tokenize(c["text"]) for c in corpus]
    doc_lens = [len(toks) for toks in corpus_tokens]
    avg_dl = sum(doc_lens) / max(len(doc_lens), 1)
    df: Dict[str, int] = {}
    for toks in corpus_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n_docs = len(corpus)
    idf = {t: math.log(1.0 + (n_docs - count + 0.5) / (count + 0.5)) for t, count in df.items()}
    return corpus_tokens, doc_lens, avg_dl, idf


def _score_bm25(query_tokens: List[str], corpus_tokens: List[List[str]], doc_lens: List[int], avg_dl: float, idf: Dict[str, float]) -> List[float]:
    k1 = 1.5
    b = 0.75
    scores = []
    for toks, dl in zip(corpus_tokens, doc_lens):
        tf_map: Dict[str, int] = {}
        for t in toks:
            tf_map[t] = tf_map.get(t, 0) + 1
        score = 0.0
        for qt in query_tokens:
            if qt in tf_map and qt in idf:
                tf = tf_map[qt]
                denom = tf + k1 * (1.0 - b + b * (dl / avg_dl))
                score += idf[qt] * (tf * (k1 + 1.0)) / denom
        scores.append(score)
    return scores


def _compute_colbert_scores(q_colbert: np.ndarray, doc_colberts: List[np.ndarray]) -> List[float]:
    scores = []
    for d_mat in doc_colberts:
        if d_mat.size == 0 or q_colbert.size == 0:
            scores.append(0.0)
            continue
        sim_mat = np.dot(q_colbert, d_mat.T)
        scores.append(float(np.sum(np.max(sim_mat, axis=1))))
    return scores


def _compute_sparse_scores(q_sparse: Dict[str, float], doc_sparses: List[Dict[str, float]]) -> List[float]:
    scores = []
    for d_sparse in doc_sparses:
        score = sum(val * d_sparse[k] for k, val in q_sparse.items() if k in d_sparse)
        scores.append(score)
    return scores


def _load_corpus_and_queries(repo_root: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pool_file = repo_root / "evals" / "authentic_candidate_pool.json"
    bench_file = repo_root / "evals" / "qrels_authentic_benchmark.json"

    raw_pool: List[Dict[str, Any]] = json.loads(pool_file.read_text(encoding="utf-8"))
    benchmark: Dict[str, Any] = json.loads(bench_file.read_text(encoding="utf-8"))
    queries: List[Dict[str, Any]] = benchmark["evaluated_queries"]

    pool: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for c in raw_pool:
        rid = c["record_id"]
        if rid not in seen_ids:
            seen_ids.add(rid)
            pool.append(c)

    for q in queries:
        for j in q["judgments"]:
            rid = j["record_id"]
            if rid not in seen_ids:
                evidence = j.get("blinded_evidence", {})
                pool.append({
                    "record_id": rid,
                    "text": evidence.get("retrieval_text", ""),
                    "project": "authentic_benchmark",
                    "source_file": evidence.get("source_id", "benchmark"),
                    "lane": evidence.get("lane", "key_point"),
                })
                seen_ids.add(rid)

    return pool, queries


def _rank_array(scores: List[float], reverse: bool = True) -> List[int]:
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=reverse)
    ranks = [0] * len(scores)
    for rank, (orig_idx, _) in enumerate(indexed, 1):
        ranks[orig_idx] = rank
    return ranks


def _fuse_query_candidates(
    q: Dict[str, Any],
    corpus: List[Dict[str, Any]],
    dense_scores: List[float],
    sparse_scores: List[float],
    colbert_scores: List[float],
    bm25_scores: List[float],
    top_k_per_engine: int = 15,
    rrf_k: int = 60,
) -> Dict[str, Any]:
    dense_ranks = _rank_array(dense_scores, reverse=True)
    sparse_ranks = _rank_array(sparse_scores, reverse=True)
    colbert_ranks = _rank_array(colbert_scores, reverse=True)
    bm25_ranks = _rank_array(bm25_scores, reverse=True)

    weights = {"dense": 0.34, "colbert": 0.34, "sparse": 0.17, "bm25": 0.15}
    fused_scores: List[float] = []
    for i in range(len(corpus)):
        score = (
            weights["dense"] / (rrf_k + dense_ranks[i])
            + weights["colbert"] / (rrf_k + colbert_ranks[i])
            + weights["sparse"] / (rrf_k + sparse_ranks[i])
            + weights["bm25"] / (rrf_k + bm25_ranks[i])
        )
        fused_scores.append(score)
    fused_ranks = _rank_array(fused_scores, reverse=True)

    judged_ids = {j["record_id"] for j in q["judgments"]}
    pooled_indices: Set[int] = set()

    for r_list in (dense_ranks, sparse_ranks, colbert_ranks, bm25_ranks, fused_ranks):
        for idx, r in enumerate(r_list):
            if r <= top_k_per_engine:
                pooled_indices.add(idx)

    for idx, c in enumerate(corpus):
        if c["record_id"] in judged_ids:
            pooled_indices.add(idx)

    candidates: List[Dict[str, Any]] = []
    seen_candidate_ids: Set[str] = set()
    for idx in sorted(pooled_indices, key=lambda i: fused_ranks[i]):
        c = corpus[idx]
        rid = c["record_id"]
        if rid in seen_candidate_ids:
            continue
        seen_candidate_ids.add(rid)
        candidates.append({
            "record_id": rid,
            "lane": c.get("lane", "key_point"),
            "record_type": "source_chunk",
            "title": f"Candidate {rid}",
            "retrieval_text": c["text"],
            "source_id": c.get("source_file"),
            "fused_rank": fused_ranks[idx],
            "fused_score": round(fused_scores[idx], 6),
            "dense_rank": dense_ranks[idx],
            "dense_score": round(dense_scores[idx], 6),
            "sparse_rank": sparse_ranks[idx],
            "sparse_score": round(sparse_scores[idx], 6),
            "bm25_rank": bm25_ranks[idx],
            "colbert_rank": colbert_ranks[idx],
            "colbert_score": round(colbert_scores[idx], 6),
        })

    return {
        "query_id": q["query_id"],
        "query_text": q["query_text"],
        "topic_id": q["topic_id"],
        "topic_name": q["topic_id"].replace("_", " ").title(),
        "tier": "authentic_benchmark",
        "domain": "authentic_production",
        "phrasing_type": "executive",
        "total_candidates_pooled": len(candidates),
        "candidates": candidates,
    }


def pool_authentic_candidates() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    corpus, queries = _load_corpus_and_queries(repo_root)
    print(f"Loaded {len(corpus)} authentic candidates and {len(queries)} authentic benchmark queries.")

    from tests.embedding_fixture import _real_backend
    backend = _real_backend()

    print("Encoding authentic corpus passages with BGE-M3...")
    corpus_texts = [c["text"] for c in corpus]
    corpus_enc = backend.encode_many(corpus_texts)

    doc_dense = np.array([enc["dense"] for enc in corpus_enc], dtype=np.float32)
    doc_dense = doc_dense / np.linalg.norm(doc_dense, axis=1, keepdims=True)
    doc_sparses = [enc["sparse"] for enc in corpus_enc]
    doc_colberts = [np.array(enc["colbert"], dtype=np.float32) for enc in corpus_enc]

    corpus_tokens, doc_lens, avg_dl, idf = _build_bm25_index(corpus)

    print("Encoding and pooling queries...")
    pooled_queries: List[Dict[str, Any]] = []

    for q in queries:
        q_text = q["query_text"]
        q_enc = backend.encode(q_text)
        q_dense = np.array(q_enc["dense"], dtype=np.float32)
        q_dense = q_dense / np.linalg.norm(q_dense)

        dense_scores = np.dot(doc_dense, q_dense).tolist()
        sparse_scores = _compute_sparse_scores(q_enc["sparse"], doc_sparses)
        colbert_scores = _compute_colbert_scores(np.array(q_enc["colbert"], dtype=np.float32), doc_colberts)
        bm25_scores = _score_bm25(_tokenize(q_text), corpus_tokens, doc_lens, avg_dl, idf)

        pooled_q = _fuse_query_candidates(
            q, corpus, dense_scores, sparse_scores, colbert_scores, bm25_scores
        )
        pooled_queries.append(pooled_q)

    out_file = repo_root / "evals" / "qrels_authentic_candidate_pool.json"
    payload = {
        "pooling_contract": "urn:nee:qrels:authentic-trec-pooling:1.0.0",
        "generated_at": "2026-10-09T10:00:00Z",
        "total_queries": len(pooled_queries),
        "total_candidates_pooled": sum(q["total_candidates_pooled"] for q in pooled_queries),
        "queries": pooled_queries,
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"✅ Successfully pooled {payload['total_candidates_pooled']} authentic candidates to: {out_file}")


if __name__ == "__main__":
    pool_authentic_candidates()
