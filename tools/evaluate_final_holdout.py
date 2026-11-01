#!/usr/bin/env python3
"""Wave 5: Final holdout evaluation and adversarial falsification attack runner."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    OperatingThresholds,
)
from dual_pipeline.retrieval_release_core.falsification import (
    evaluate_holdout_partition,
    execute_falsification_attack_citation_only_authority,
    execute_falsification_attack_clearance_breach,
    execute_falsification_attack_corrupt_sidecar,
    execute_falsification_attack_corrupt_vector,
    execute_falsification_attack_disable_bge_m3,
    execute_falsification_attack_disable_bge_m3_with_cleared_caches,
    execute_falsification_attack_inject_wrong_facts,
    execute_falsification_attack_remove_positives,
    execute_falsification_attack_reverse_rankings,
    execute_falsification_attack_superseded_contradicted,
)
from dual_pipeline.retrieval_release_core.paired_experiments import QueryJudgments


def _load_authentic_holdout_data(
    benchmark_path: Path,
    candidate_pool_path: Path,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    with open(benchmark_path, "r", encoding="utf-8") as f:
        benchmark_data = json.load(f)
    with open(candidate_pool_path, "r", encoding="utf-8") as f:
        pool_data = json.load(f)

    pool_map = {q["query_id"]: q for q in pool_data["queries"]}
    holdout_queries_data: List[Dict[str, Any]] = []
    ground_truth_map: Dict[str, QueryJudgments] = {}
    topic_set = set()

    for q in benchmark_data["evaluated_queries"]:
        qid = q["query_id"]
        judgments = {}
        relevant_ids = set()
        for j in q.get("judgments", []):
            rec_id = j["record_id"]
            gr = j["multi_dimensional_label"]["evidence_relevance"]["grade"]
            judgments[rec_id] = gr
            if gr >= 1:
                relevant_ids.add(rec_id)
        ground_truth_map[qid] = QueryJudgments(
            query_id=qid,
            query_text=q["query_text"],
            topic_id=q["topic_id"],
            phrasing_type=q.get("phrasing_type", "executive"),
            judgments=judgments,
            relevant_ids=relevant_ids,
            is_answerable=True,
        )
        if qid in pool_map:
            q_copy = dict(pool_map[qid])
            q_copy["partition"] = "final_holdout"
            holdout_queries_data.append(q_copy)
            topic_set.add(q["topic_id"])

    partitions = {
        "query_partitions": {"final_holdout": [q["query_id"] for q in holdout_queries_data]},
        "topic_assignments": {"final_holdout": sorted(list(topic_set))},
    }
    return holdout_queries_data, ground_truth_map, partitions


def _load_legacy_holdout_data(
    repo_root: Path,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    partitions_path = repo_root / "evals" / "qrels_partitions.json"
    with open(partitions_path, "r", encoding="utf-8") as f:
        partitions = json.load(f)

    holdout_query_ids = partitions["query_partitions"]["final_holdout"]

    gt_path = repo_root / "evals" / "qrels_independent_ground_truth.json"
    with open(gt_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)

    pool_path = repo_root / "evals" / "qrels_candidate_pool.json"
    with open(pool_path, "r", encoding="utf-8") as f:
        pool_data = json.load(f)

    pool_map = {q["query_id"]: q for q in pool_data["queries"]}
    holdout_queries_data: List[Dict[str, Any]] = []
    ground_truth_map: Dict[str, QueryJudgments] = {}

    for q in gt_data["evaluated_queries"]:
        qid = q["query_id"]
        if qid not in holdout_query_ids:
            continue
        judgments = {}
        relevant_ids = set()
        is_answerable = True
        for c in q["judged_candidates"]:
            rec_id = c["record_id"]
            gr = c["multi_dimensional_label"]["evidence_relevance"]["grade"]
            judgments[rec_id] = gr
            if gr >= 1:
                relevant_ids.add(rec_id)
            if c["multi_dimensional_label"]["answerability"]["status"] == "UNANSWERABLE":
                is_answerable = False
        ground_truth_map[qid] = QueryJudgments(
            query_id=qid,
            query_text=q["query_text"],
            topic_id=q["topic_id"],
            phrasing_type=q["phrasing_type"],
            judgments=judgments,
            relevant_ids=relevant_ids,
            is_answerable=is_answerable,
        )
        if qid in pool_map:
            q_copy = dict(pool_map[qid])
            q_copy["partition"] = "final_holdout"
            holdout_queries_data.append(q_copy)

    return holdout_queries_data, ground_truth_map, partitions


def load_final_holdout_data(
    repo_root: Path,
    benchmark_path: Path | None = None,
    candidate_pool_path: Path | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    if benchmark_path is not None and candidate_pool_path is not None:
        return _load_authentic_holdout_data(benchmark_path, candidate_pool_path)
    return _load_legacy_holdout_data(repo_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Wave 5 holdout evaluation and falsification attacks.")
    parser.add_argument(
        "--benchmark-dataset",
        type=Path,
        default=None,
        help="Optional path to authentic benchmark dataset.",
    )
    parser.add_argument(
        "--candidate-pool",
        type=Path,
        default=None,
        help="Optional path to authentic candidate pool.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "reports" / "retrieval_calibration_wave5_holdout_falsification_report.json",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("Wave 5: Attempt to Falsify the Frozen System (Final Holdout Evaluation)")
    print("=" * 80)

    holdout_queries, ground_truth_map, partitions = load_final_holdout_data(
        REPO_ROOT,
        benchmark_path=args.benchmark_dataset,
        candidate_pool_path=args.candidate_pool,
    )
    print(f"Loaded {len(holdout_queries)} untouched holdout queries across {len(partitions['topic_assignments']['final_holdout'])} topics.")

    if args.benchmark_dataset is not None:
        weights = CalibrationModelWeights.for_authentic_benchmark()
        thresholds = OperatingThresholds.for_authentic_benchmark()
    else:
        weights = CalibrationModelWeights()
        thresholds = OperatingThresholds()

    # 1. Single Holdout Evaluation
    print("\n--- 1. Single Evaluation on Locked Final Holdout ---")
    holdout_metrics = evaluate_holdout_partition(
        holdout_queries, ground_truth_map, weights, thresholds, cutoffs=(5, 10, 20)
    )
    print(f"  Holdout Queries Evaluated: {holdout_metrics['queries_evaluated']}")
    print(f"  Candidate Recall:          {holdout_metrics['mean_candidate_recall']:.4f}")
    for k in (5, 10, 20):
        m = holdout_metrics["metrics_at_k"][k]
        print(f"  Cutoff k={k:2d}: nDCG={m['ndcg']:.4f} | MRR={m['mrr']:.4f} | Recall={m['recall']:.4f} | Unjudged={m['unjudged']:.4f}")
    print(f"  Decisions Breakdown:       {holdout_metrics['decisions_breakdown']}")

    # 2. Stress & Falsification Attacks
    print("\n--- 2. Executing Adversarial Falsification Attacks ---")
    attack_results = []
    sample_query = next(
        (
            q
            for q in holdout_queries
            if any(
                r in ground_truth_map[q["query_id"]].relevant_ids
                for r in [c["record_id"] for c in q.get("candidates", [])[:10]]
            )
        ),
        holdout_queries[0],
    )
    sample_gt = ground_truth_map[sample_query["query_id"]]

    # Attack 1: Remove positives
    att1 = execute_falsification_attack_remove_positives(sample_query, sample_gt)
    attack_results.append(att1)
    print(f"  [{att1.attack_type.value}] Clean={att1.detected_cleanly} | Degradation={att1.graceful_degradation} | {att1.details}")

    # Attack 2: Inject plausible wrong facts
    att2 = execute_falsification_attack_inject_wrong_facts(sample_query, sample_gt, weights, thresholds)
    attack_results.append(att2)
    print(f"  [{att2.attack_type.value}] Clean={att2.detected_cleanly} | Degradation={att2.graceful_degradation} | {att2.details}")

    # Attack 3: Reverse rankings
    att3 = execute_falsification_attack_reverse_rankings(sample_query, sample_gt)
    attack_results.append(att3)
    print(f"  [{att3.attack_type.value}] Clean={att3.detected_cleanly} | Degradation={att3.graceful_degradation} | {att3.details}")

    # Attack 4: Corrupt vector dimensions
    att4 = execute_falsification_attack_corrupt_vector([0.1] * 1024)
    attack_results.append(att4)
    print(f"  [{att4.attack_type.value}] Clean={att4.detected_cleanly} | ForgedPassPrevented={att4.forged_pass_prevented} | {att4.details}")

    # Attack 5: Disable BGE-M3 with zero credit & cleared caches
    att5 = execute_falsification_attack_disable_bge_m3_with_cleared_caches()
    attack_results.append(att5)
    print(f"  [{att5.attack_type.value}] Clean={att5.detected_cleanly} | ForgedPassPrevented={att5.forged_pass_prevented} | {att5.details}")

    # Attack 6: Clearance breach attempt
    att6 = execute_falsification_attack_clearance_breach()
    attack_results.append(att6)
    print(f"  [{att6.attack_type.value}] Clean={att6.detected_cleanly} | ForgedPassPrevented={att6.forged_pass_prevented} | {att6.details}")

    # Attack 7: Citation only authority injection
    att7 = execute_falsification_attack_citation_only_authority()
    attack_results.append(att7)
    print(f"  [{att7.attack_type.value}] Clean={att7.detected_cleanly} | ForgedPassPrevented={att7.forged_pass_prevented} | {att7.details}")

    # Attack 8: Superseded and contradicted assertions
    att8 = execute_falsification_attack_superseded_contradicted()
    attack_results.append(att8)
    print(f"  [{att8.attack_type.value}] Clean={att8.detected_cleanly} | ForgedPassPrevented={att8.forged_pass_prevented} | {att8.details}")

    # Attack 9: Corrupt sidecar validation
    att9 = execute_falsification_attack_corrupt_sidecar()
    attack_results.append(att9)
    print(f"  [{att9.attack_type.value}] Clean={att9.detected_cleanly} | ForgedPassPrevented={att9.forged_pass_prevented} | {att9.details}")

    all_attacks_safely_handled = all(a.detected_cleanly and a.forged_pass_prevented for a in attack_results)
    print(f"\nAll Falsification Attacks Detected & Handled Safely: {all_attacks_safely_handled}")

    full_report = {
        "report_version": "1.0.0",
        "wave": "Wave 5: Attempt to falsify the frozen system",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "partition_evaluated": "final_holdout",
        "total_holdout_queries": len(holdout_queries),
        "total_holdout_topics": len(partitions["topic_assignments"]["final_holdout"]),
        "holdout_evaluation_metrics": holdout_metrics,
        "falsification_attacks": [
            {
                "attack_type": a.attack_type.value,
                "detected_cleanly": a.detected_cleanly,
                "graceful_degradation": a.graceful_degradation,
                "forged_pass_prevented": a.forged_pass_prevented,
                "details": a.details,
                "metrics_before": a.metrics_before,
                "metrics_after": a.metrics_after,
            }
            for a in attack_results
        ],
        "all_attacks_safely_handled": all_attacks_safely_handled,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)
    print(f"\n✅ Holdout and falsification report successfully saved to: {args.output}")

    return 0 if all_attacks_safely_handled else 1


if __name__ == "__main__":
    sys.exit(main())
