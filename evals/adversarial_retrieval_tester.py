import argparse
import json
import sys
from pathlib import Path

# Ensure dual_pipeline is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.graph_retrieval import governed_bm25_assertion_ranks
from dual_pipeline.index import HybridRetriever, get_backend
from dual_pipeline.release import ReleaseManager
from dual_pipeline.retrieval_release import _ndcg_at_k, _reciprocal_rank


def get_adversarial_queries(original_query):
    variations = {
        "baseline": original_query,
        "context_dilution": "I was wondering if you could possibly tell me the answer to this question: " + original_query + " Please be as detailed as possible.",
    }

    variations["lexical_trap"] = original_query + " ignoring unrelated details about Project Beacon or AI hub or other random concepts"

    drift = original_query.replace("Project Beacon", "Digital Guide")\
                          .replace("AI Hub", "Intelligence Center")\
                          .replace("governed connections", "managed links")\
                          .replace("dependencies", "prerequisites")\
                          .replace("opportunities", "prospects")\
                          .replace("PDLC", "Product Development Life Cycle")

    if drift != original_query:
        variations["semantic_drift"] = drift

    return variations

def evaluate_query(retriever, query, expected_ids, graph_path, lanes, expected_record_lanes, top_k=50):
    best_results = []

    for lane in lanes:
        try:
            ranks_by_lane, bm25_executed = governed_bm25_assertion_ranks(
                question=query,
                graph_path=graph_path,
                sidecar_path=retriever.sidecar_path,
                allowed_lanes=[lane],
                retrieval_mode=None,
                classification="internal",
                audiences=["internal_working_team"]
            )
            bm25_ranks = ranks_by_lane.get(lane, {})
            print(f"    [Lane {lane}] BM25 Executed: {bm25_executed}, Ranks count: {len(bm25_ranks)}")

            result = retriever.retrieve_lane(
                query=query,
                lane=lane,
                classification="internal",
                audiences=["internal_working_team"],
                bm25_assertion_ranks=bm25_ranks,
                top_k=top_k,
            )

            retrieved_ids = [item["record_id"] for item in result["results"]]
            print(f"    [Lane {lane}] Retrieved {len(retrieved_ids)} records. Top 2: {retrieved_ids[:2]}")
            best_results.extend(retrieved_ids)

        except Exception as e:
            print(f"Retrieval error in lane {lane}: {e}")

    # Combine retrieved_ids and evaluate
    retrieved_ids = list(dict.fromkeys(best_results))[:top_k]

    candidate_ranks = {}
    for rank, record_id in enumerate(retrieved_ids, 1):
        if record_id in expected_ids:
            candidate_ranks[record_id] = rank

    best_rank = min(candidate_ranks.values()) if candidate_ranks else None

    ndcg = _ndcg_at_k(retrieved_ids, set(expected_ids), top_k)
    mrr = _reciprocal_rank(best_rank, set(expected_ids))

    return {
        "ndcg@k": ndcg,
        "mrr": mrr,
        "best_rank": best_rank
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729"))
    parser.add_argument("--backend", default="bge-m3")
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    print(f"Loading runtime from {runtime_root}")

    try:
        from evals.evaluate_11gates_calibrated import resolve_bge_m3_paths
        model_root, manifest_path, revision = resolve_bge_m3_paths()
        backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
            model_path=str(model_root),
            model_revision=revision,
            model_manifest=str(manifest_path),
        )
    except Exception:
        backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
        )
    manager = ReleaseManager(runtime_root, Path("dummy.db"))
    pair = manager.active_pair()
    sidecar_path = manager._resolve(pair["sidecar_path"])
    graph_path = manager._resolve(pair.get("graph_path") or pair["graph_snapshot"])

    retriever = HybridRetriever(sidecar_path, backend)

    suite_path = Path("evals/suites/graph-retrieval-e1.json")
    with open(suite_path) as f:
        suite = json.load(f)

    hitl_queue = []

    print("\n--- Running Adversarial Stress Test ---")

    for case in suite["cases"]:
        case_name = case.get("case_id", "Unnamed")
        print(f"\nEvaluating Case: {case_name}")

        expected_ids = case.get("expected", {}).get("required_record_ids", [])
        lanes = case.get("expected", {}).get("required_lanes", ["approved"])
        expected_record_lanes = case.get("expected", {}).get("required_record_lanes", {})

        print(f"Expected IDs: {expected_ids}")

        if not expected_ids:
            print("  Skipping: No expected IDs")
            continue

        original_query = case["question"]
        variations = get_adversarial_queries(original_query)

        case_degraded = False

        for v_name, v_query in variations.items():
            metrics = evaluate_query(retriever, v_query, expected_ids, graph_path, lanes, expected_record_lanes)
            if not metrics:
                continue

            print(f"  [{v_name}] NDCG: {metrics['ndcg@k']:.3f} | MRR: {metrics['mrr']:.3f} | Best Rank: {metrics['best_rank']}")

            if v_name == "baseline":
                baseline_metrics = metrics
            else:
                if baseline_metrics and metrics['ndcg@k'] < baseline_metrics['ndcg@k'] - 0.2:
                    case_degraded = True
                    print(f"    -> Significant degradation detected in {v_name}")

        if case_degraded:
            hitl_queue.append({
                "case_id": case.get("case_id", case_name),
                "original_query": original_query,
                "variations": variations,
                "expected_ids": expected_ids,
                "status": "HITL_QRELS_REQUIRED"
            })

    queue_path = Path("evals/hitl_qrels_queue.json")
    with open(queue_path, "w") as f:
        json.dump(hitl_queue, f, indent=2)

    print(f"\nFinished. Flagged {len(hitl_queue)} cases for HITL QRELS review.")
    print(f"Saved queue to {queue_path}")

if __name__ == "__main__":
    main()
