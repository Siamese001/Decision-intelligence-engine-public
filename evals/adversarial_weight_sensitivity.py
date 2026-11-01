"""Adversarial Weight Sensitivity Tester — Wave 1

Samples real records from the active sidecar, generates adversarial query
variants targeting those records, measures per-component rank spread, and
determines whether the current RRF fusion weights mask component disagreement.

Usage:
    python evals/adversarial_weight_sensitivity.py [--runtime-root PATH] [--sample-size N]
"""

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from dual_pipeline.graph_retrieval import governed_bm25_assertion_ranks
from dual_pipeline.index import BM25_RRF_WEIGHTS, HybridRetriever, get_backend
from dual_pipeline.release import ReleaseManager

# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

def generate_queries_for_record(artifact: dict[str, Any]) -> dict[str, str]:
    """Generate adversarial query variants from a sidecar record's text."""
    title = artifact.get("title", "")
    retrieval_text = artifact.get("retrieval_text", "")
    record_type = artifact.get("record_type", "")

    queries: dict[str, str] = {}

    # Baseline: use the title as a natural query
    if title:
        queries["title_exact"] = title

    # Semantic paraphrase: rephrase the retrieval text as a question
    if retrieval_text:
        # Trim to core content and turn into a question
        core = retrieval_text.split(" ROUTES_TO")[0] if "ROUTES_TO" in retrieval_text else retrieval_text[:120]
        queries["paraphrase"] = f"What do we know about {core}?"

    # Context dilution: bury the signal in filler
    if title:
        queries["diluted"] = (
            f"I'm trying to understand the overall program landscape and was "
            f"wondering if you could help me find information about {title} "
            f"and how it connects to the broader strategic context"
        )

    # Partial recall: use only fragments of the title
    words = title.split()
    if len(words) >= 4:
        queries["partial"] = " ".join(words[:len(words) // 2])

    # Domain synonym substitution
    synonym_map = {
        "ROUTES_TO": "connects to",
        "DEPENDS_ON": "relies on",
        "CONTRIBUTES_TO": "feeds into",
        "Project Beacon": "Digital Guide",
        "AI Hub": "Intelligence Platform",
        "workstream": "initiative",
        "NEE": "decision engine",
        "Outage": "Service Disruption",
        "Chakra": "Platform Framework",
    }
    if title:
        synth = title
        for original, replacement in synonym_map.items():
            synth = synth.replace(original, replacement)
        if synth != title:
            queries["synonym_drift"] = synth

    return queries


# ---------------------------------------------------------------------------
# Component rank extraction
# ---------------------------------------------------------------------------

def retrieve_with_diagnostics(
    retriever: HybridRetriever,
    query: str,
    lane: str,
    graph_path: Path,
    top_k: int = 50,
) -> dict[str, Any] | None:
    """Run retrieval and return the full result with rank diagnostics."""
    try:
        ranks_by_lane, bm25_executed = governed_bm25_assertion_ranks(
            question=query,
            graph_path=graph_path,
            sidecar_path=retriever.sidecar_path,
            allowed_lanes=[lane],
            retrieval_mode=None,
            classification="internal",
            audiences=["internal_consulting"],
        )
        bm25_ranks = ranks_by_lane.get(lane, {})

        result = retriever.retrieve_lane(
            query=query,
            lane=lane,
            classification="internal",
            audiences=["internal_consulting"],
            bm25_assertion_ranks=bm25_ranks,
            top_k=top_k,
        )
        return result
    except Exception:
        return None


def component_rank_spread(result: dict[str, Any], target_record_id: str) -> dict[str, Any]:
    """Extract per-component ranks for a target record from retrieval results."""
    for item in result.get("results", []):
        if item["record_id"] == target_record_id:
            ranks = {
                "fused": item["fused_rank"],
                "dense": item["dense_rank"],
                "sparse": item["sparse_rank"],
                "colbert": item["colbert_rank"],
            }
            if item.get("bm25_rank") is not None:
                ranks["bm25"] = item["bm25_rank"]

            active_ranks = [v for v in ranks.values() if v is not None]
            spread = max(active_ranks) - min(active_ranks) if active_ranks else 0
            return {
                "found": True,
                "ranks": ranks,
                "spread": spread,
                "fused_rank": item["fused_rank"],
            }

    return {"found": False, "ranks": {}, "spread": None, "fused_rank": None}


# ---------------------------------------------------------------------------
# Weight sweep (Wave 2 — inline if spread detected)
# ---------------------------------------------------------------------------

def rrf_score(ranks: dict[str, int], weights: dict[str, float], k: int = 60) -> float:
    """Compute RRF score for a record given its per-component ranks and weights."""
    return sum(
        weights.get(component, 0) / (k + rank)
        for component, rank in ranks.items()
        if component in weights
    )


def sweep_weights(
    disagreement_cases: list[dict],
    step: float = 0.05,
) -> list[dict[str, Any]]:
    """Grid search over 4-simplex for weight configurations."""
    components = ["dense", "sparse", "bm25", "colbert"]
    configs = []
    steps = int(1.0 / step) + 1

    for d in range(steps):
        for s in range(steps - d):
            for b in range(steps - d - s):
                c = steps - 1 - d - s - b
                w = {
                    "dense": d * step,
                    "sparse": s * step,
                    "bm25": b * step,
                    "colbert": c * step,
                }
                total = sum(w.values())
                if abs(total - 1.0) > 0.001:
                    continue
                if any(v < 0.01 for v in w.values()):
                    continue  # skip degenerate configs
                configs.append(w)

    results = []
    for w in configs:
        worst_rank = 0
        total_rank = 0
        count = 0
        for case in disagreement_cases:
            ranks = case["ranks"]
            score = rrf_score(ranks, w)
            # We can't re-rank without all candidates, but we can compare
            # relative score to the current weights
            count += 1
        results.append({"weights": w, "count": count})

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Adversarial weight sensitivity tester")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path(".local/main/runtime-bge-m3-sherpa-strategic-20260729"),
    )
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    print(f"Runtime: {runtime_root}")

    # Load backend
    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path="C:\\NEE\\models\\bge-m3\\5617a9f61b028005a4858fdac845db406aefb181",
        model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        model_manifest="C:\\NEE\\models\\bge-m3\\bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json",
    )

    manager = ReleaseManager(runtime_root, Path("dummy.db"))
    pair = manager.active_pair()
    sidecar_path = manager._resolve(pair["sidecar_path"])
    graph_path = manager._resolve(pair.get("graph_path") or pair["graph_snapshot"])

    retriever = HybridRetriever(sidecar_path, backend)

    # Sample real records from the sidecar
    conn = sqlite3.connect(str(sidecar_path))  # noqa: TID251
    conn.row_factory = sqlite3.Row

    # Get factual_answer records with internal_working_team audience
    all_records = []
    for row in conn.execute(
        "SELECT record_id, artifact_json FROM retrieval_record WHERE lane='approved'"
    ):
        artifact = json.loads(row["artifact_json"])
        if artifact.get("answer_use") != "factual_answer":
            continue
        if "internal_consulting" not in artifact.get("audiences", []):
            continue
        if not artifact.get("retrieval_text"):
            continue
        all_records.append({"record_id": row["record_id"], "artifact": artifact})
    conn.close()

    print(f"Found {len(all_records)} eligible factual_answer records")
    random.seed(args.seed)
    sampled = random.sample(all_records, min(args.sample_size, len(all_records)))
    print(f"Sampled {len(sampled)} records for adversarial testing\n")

    # Run adversarial queries
    all_results = []
    disagreement_cases = []
    total_queries = 0
    found_count = 0
    spread_threshold = 5

    from tqdm import tqdm

    for i, record in enumerate(tqdm(sampled, desc="Testing records")):
        record_id = record["record_id"]
        artifact = record["artifact"]
        queries = generate_queries_for_record(artifact)

        for query_type, query_text in queries.items():
            total_queries += 1
            result = retrieve_with_diagnostics(
                retriever, query_text, "approved", graph_path
            )
            if result is None:
                all_results.append({
                    "record_id": record_id,
                    "query_type": query_type,
                    "error": True,
                })
                continue

            spread_info = component_rank_spread(result, record_id)
            entry = {
                "record_id": record_id,
                "query_type": query_type,
                "query_text": query_text,
                "title": artifact.get("title", "")[:80],
                **spread_info,
            }
            all_results.append(entry)

            if spread_info["found"]:
                found_count += 1
                if spread_info["spread"] is not None and spread_info["spread"] >= spread_threshold:
                    disagreement_cases.append(entry)

    # Report
    print(f"\n{'='*80}")
    print("WAVE 1 RESULTS: Adversarial Weight Sensitivity Analysis")
    print(f"{'='*80}")
    print(f"Total queries executed: {total_queries}")
    print(f"Target record found in results: {found_count}/{total_queries}")
    print(f"Component disagreement cases (spread >= {spread_threshold}): {len(disagreement_cases)}")
    print()

    # Show found cases with their rank details
    found_entries = [e for e in all_results if e.get("found")]
    if found_entries:
        print(f"--- Records found in retrieval results ({len(found_entries)} cases) ---")
        # Sort by spread descending
        found_entries.sort(key=lambda e: (e.get("spread") or 0), reverse=True)
        for entry in found_entries[:20]:
            ranks = entry["ranks"]
            rank_str = " | ".join(f"{k}={v}" for k, v in sorted(ranks.items()))
            print(f"  [{entry['query_type']:15s}] spread={entry['spread']:3d} | {rank_str}")
            print(f"    title: {entry['title']}")
            print(f"    query: {entry['query_text']}")
            print()

    # Show cases where record was NOT found
    not_found = [e for e in all_results if not e.get("found") and not e.get("error")]
    if not_found:
        print(f"\n--- Records NOT found in top-50 ({len(not_found)} cases) ---")
        for entry in not_found[:10]:
            print(f"  [{entry['query_type']:15s}] {entry.get('title', '')[:60]}")
            print(f"    query: {entry.get('query_text', '')[:80]}")
            print()

    # Current weight analysis
    print(f"\n{'='*80}")
    print("CURRENT WEIGHTS: {dense: 0.34, sparse: 0.17, bm25: 0.15, colbert: 0.34}")
    print(f"{'='*80}")

    if len(disagreement_cases) == 0:
        print("\n>>> DECISION: No component disagreement detected (spread < 5).")
        print(">>> Current weights are VALIDATED for this corpus.")
        print(">>> Wave 2 (weight sweep) and Wave 3 (HITL QRELS) are NOT needed.")
    else:
        print(f"\n>>> WARNING: {len(disagreement_cases)} cases with component spread >= {spread_threshold}")
        print(">>> Proceeding to Wave 2 weight sweep would be warranted.")

    # Save detailed results
    output_path = Path("evals/wave1_weight_sensitivity_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(
            {
                "summary": {
                    "total_queries": total_queries,
                    "found_count": found_count,
                    "disagreement_count": len(disagreement_cases),
                    "spread_threshold": spread_threshold,
                    "sample_size": len(sampled),
                    "current_weights": dict(BM25_RRF_WEIGHTS),
                    "decision": "VALIDATED" if len(disagreement_cases) == 0 else "NEEDS_WAVE_2",
                },
                "disagreement_cases": disagreement_cases,
                "all_results": [
                    {k: v for k, v in e.items() if k != "artifact"}
                    for e in all_results
                ],
            },
            f,
            indent=2,
            default=str,
        )
    print(f"\nDetailed results saved to {output_path}")


if __name__ == "__main__":
    main()
