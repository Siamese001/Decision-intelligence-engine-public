"""Wave 3B: 4-Engine TREC Candidate Pooling & Diagnostics

Executes 4-engine candidate retrieval across Dense, Learned Sparse, ColBERT MaxSim,
and Graph BM25 for all 50 cosine-stratified queries in evals/qrels_query_set.json.

Queries both 'approved' (entity facets, cards, edges) and 'candidate' (source chunks,
raw meeting transcript passages) sidecar lanes to build the comprehensive evaluation pool.

Extracts full passage text, source locators, component scores, and ranks for
grounded human/automated relevance judgments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.graph_retrieval import governed_bm25_assertion_ranks
from dual_pipeline.index import (
    BM25_RRF_WEIGHTS,
    HybridRetriever,
    get_backend,
)
from dual_pipeline.release import ReleaseManager


def run_trec_candidate_pooling(
    runtime_root: Path,
    query_set_path: Path,
    output_path: Path,
    top_k_per_engine: int = 10,
    model_path: str = r"C:\NEE\models\bge-m3\5617a9f61b028005a4858fdac845db406aefb181",
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181",
    model_manifest: str = r"C:\NEE\models\bge-m3\bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json",
) -> dict[str, Any]:
    print("=" * 80)
    print("Wave 3B: 4-Engine TREC Candidate Pooling (Depth K=10)")
    print("=" * 80)
    start_time = time.time()

    # 1. Load Query Set
    if not query_set_path.exists():
        raise FileNotFoundError(f"Query set not found at: {query_set_path}")
    with open(query_set_path, "r", encoding="utf-8") as f:
        query_set = json.load(f)

    queries = query_set.get("queries", [])
    print(f"Loaded {len(queries)} stratified queries from {query_set_path}")

    # 2. Initialize Retriever & Backend
    print(f"\nInitializing BGE-M3 backend from {model_path}...")
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path=model_path,
        model_revision=model_revision,
        model_manifest=model_manifest,
    )

    manager = ReleaseManager(runtime_root, Path("dummy.db"))
    pair = manager.active_pair()
    sidecar_path = manager._resolve(pair["sidecar_path"])
    graph_path = manager._resolve(pair.get("graph_path") or pair["graph_snapshot"])

    print(f"Active pair sidecar: {sidecar_path}")
    print(f"Active pair graph:   {graph_path}")
    retriever = HybridRetriever(sidecar_path, backend)

    # 3. Candidate Pooling loop across all queries
    pooled_queries: list[dict[str, Any]] = []
    total_candidates_pooled = 0

    print("\nRunning 4-engine retrieval across approved and candidate lanes...")
    with tqdm(total=len(queries), desc="Pooling Candidates", unit="query") as pbar:
        for q in queries:
            query_id = q["query_id"]
            query_text = q["query_text"]

            lane_candidates: dict[str, dict[str, Any]] = {}

            for lane in ["approved", "candidate"]:
                try:
                    ranks_by_lane, _ = governed_bm25_assertion_ranks(
                        question=query_text,
                        graph_path=graph_path,
                        sidecar_path=retriever.sidecar_path,
                        allowed_lanes=[lane],
                        retrieval_mode=None,
                        classification="internal",
                        audiences=["internal_consulting"],
                    )
                    bm25_ranks = ranks_by_lane.get(lane, {})

                    result = retriever.retrieve_lane(
                        query=query_text,
                        lane=lane,
                        classification="internal",
                        audiences=["internal_consulting"],
                        bm25_assertion_ranks=bm25_ranks,
                        top_k=30,
                    )

                    for item in result.get("results", []):
                        rec_id = item["record_id"]
                        rec = item.get("record", {})
                        lane_candidates[rec_id] = {
                            "record_id": rec_id,
                            "lane": lane,
                            "record_type": rec.get("record_type"),
                            "answer_use": rec.get("answer_use"),
                            "title": rec.get("title", ""),
                            "retrieval_text": rec.get("retrieval_text", ""),
                            "source_id": rec.get("source_id"),
                            "fused_rank": item.get("fused_rank"),
                            "fused_score": item.get("fused_score"),
                            "dense_rank": item.get("dense_rank"),
                            "dense_score": item.get("dense_score"),
                            "sparse_rank": item.get("sparse_rank"),
                            "sparse_score": item.get("sparse_score"),
                            "bm25_rank": item.get("bm25_rank"),
                            "colbert_rank": item.get("colbert_rank"),
                            "colbert_score": item.get("colbert_score"),
                        }
                except Exception:
                    pass

            # Engine-specific top-K selections
            all_recs = list(lane_candidates.values())

            dense_top = sorted(
                [r for r in all_recs if r.get("dense_score") is not None],
                key=lambda x: -x["dense_score"],
            )[:top_k_per_engine]

            sparse_top = sorted(
                [r for r in all_recs if r.get("sparse_score") is not None],
                key=lambda x: -x["sparse_score"],
            )[:top_k_per_engine]

            colbert_top = sorted(
                [r for r in all_recs if r.get("colbert_score") is not None],
                key=lambda x: -x["colbert_score"],
            )[:top_k_per_engine]

            bm25_top = sorted(
                [r for r in all_recs if r.get("bm25_rank") is not None],
                key=lambda x: x["bm25_rank"],
            )[:top_k_per_engine]

            fused_top = sorted(
                [r for r in all_recs if r.get("fused_rank") is not None],
                key=lambda x: x["fused_rank"],
            )[:top_k_per_engine]

            # Pool = Union of top-K across components
            pooled_ids = set()
            for r in dense_top + sparse_top + colbert_top + bm25_top + fused_top:
                pooled_ids.add(r["record_id"])

            pooled_list = [lane_candidates[rid] for rid in pooled_ids if rid in lane_candidates]
            # Order pooled candidates by fused rank for presentation consistency
            pooled_list.sort(key=lambda x: (x.get("fused_rank") or 999, -(x.get("dense_score") or 0.0)))

            total_candidates_pooled += len(pooled_list)
            pooled_queries.append({
                "query_id": query_id,
                "query_text": query_text,
                "topic_id": q["topic_id"],
                "topic_name": q["topic_name"],
                "tier": q["tier"],
                "domain": q["domain"],
                "phrasing_type": q["phrasing_type"],
                "grounding_source": q["grounding_source"],
                "ground_truth_summary": q["ground_truth_summary"],
                "target_entities": q["target_entities"],
                "distractor_entity": q.get("distractor_entity"),
                "total_candidates_pooled": len(pooled_list),
                "candidates": pooled_list,
            })
            pbar.update(1)

    elapsed = time.time() - start_time
    avg_per_query = total_candidates_pooled / max(len(queries), 1)

    # 4. Save Candidate Pool JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pooling_contract": "urn:nee:qrels:trec-pooling:1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_queries": len(pooled_queries),
        "total_candidates_pooled": total_candidates_pooled,
        "average_candidates_per_query": round(avg_per_query, 1),
        "top_k_per_engine": top_k_per_engine,
        "canonical_weights": BM25_RRF_WEIGHTS,
        "queries": pooled_queries,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    receipt_path = output_path.with_name("wave3b_trec_pooling_receipt.json")
    receipt = {
        "wave": "Wave 3B",
        "status": "COMPLETE",
        "total_queries": len(pooled_queries),
        "total_candidates_pooled": total_candidates_pooled,
        "average_candidates_per_query": round(avg_per_query, 1),
        "output_file": str(output_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
    }
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print("\n" + "=" * 80)
    print("TREC POOLING SUMMARY")
    print("=" * 80)
    print(f"Total queries processed:       {len(pooled_queries)}")
    print(f"Total candidates pooled:       {total_candidates_pooled}")
    print(f"Average candidates per query:  {avg_per_query:.1f}")
    print(f"Saved candidate pool to:       {output_path}")
    print(f"Recorded receipt to:           {receipt_path}")
    print(f"Total elapsed:                 {elapsed:.2f}s")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wave 3B TREC Candidate Pooling")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729"),
    )
    parser.add_argument(
        "--query-set",
        type=Path,
        default=Path("evals/qrels_query_set.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/qrels_candidate_pool.json"),
    )
    args = parser.parse_args()
    run_trec_candidate_pooling(args.runtime_root, args.query_set, args.output)
