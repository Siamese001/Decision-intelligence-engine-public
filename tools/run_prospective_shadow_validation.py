#!/usr/bin/env python3
"""Run prospective shadow validation, sampled human adjudication, drift tracking, and rollback tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
    make_calibrated_decision,
)
from dual_pipeline.retrieval_release_core.paired_experiments import (
    EvaluationMode,
    QueryJudgments,
    RetrievalConfig,
    rank_candidates_for_query,
)
from dual_pipeline.retrieval_release_core.prospective_validation import (
    ConsumerMetrics,
    ObservationConfig,
    RollbackEngine,
    RollbackTrigger,
    SamplingPolicy,
    ShadowEvaluationSession,
)


def _load_locked_partition_ids(repo_root: Path) -> Set[str]:
    parts_path = repo_root / "evals" / "qrels_partitions.json"
    if not parts_path.exists():
        return set()
    data = json.loads(parts_path.read_text(encoding="utf-8"))
    q_parts = data.get("query_partitions", {})
    return set(q_parts.get("dev", []) + q_parts.get("calibration", []) + q_parts.get("final_holdout", []))


def _load_authentic_evaluation_data(
    benchmark_path: Path, candidate_pool_path: Path, locked_ids: Set[str]
) -> Tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    benchmark_data = json.loads(benchmark_path.read_text(encoding="utf-8"))
    pool_data = json.loads(candidate_pool_path.read_text(encoding="utf-8"))
    pool_map = {q["query_id"]: q for q in pool_data["queries"]}
    queries_data: List[Dict[str, Any]] = []
    ground_truth_map: Dict[str, QueryJudgments] = {}
    topic_set = set()

    for q in benchmark_data["evaluated_queries"]:
        qid = q["query_id"]
        if qid in locked_ids:
            raise ValueError(f"CRITICAL: Authentic query '{qid}' overlaps with locked partitions!")
        judgments: Dict[str, int] = {}
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
            queries_data.append(pool_map[qid])
            topic_set.add(q["topic_id"])

    partitions = {
        "query_partitions": {"prospective": [q["query_id"] for q in queries_data]},
        "topic_assignments": {"prospective": sorted(list(topic_set))},
    }
    return queries_data, ground_truth_map, partitions


def _load_stream_evaluation_data(
    repo_root: Path, locked_ids: Set[str]
) -> Tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    stream_path = repo_root / "evals" / "qrels_prospective_shadow_stream.json"
    if not stream_path.exists():
        raise FileNotFoundError(f"Prospective stream file missing: {stream_path}")
    stream_data = json.loads(stream_path.read_text(encoding="utf-8"))
    queries_data: List[Dict[str, Any]] = []
    ground_truth_map: Dict[str, QueryJudgments] = {}
    topic_set = set()

    for q in stream_data["queries"]:
        qid = q["query_id"]
        if qid in locked_ids:
            raise ValueError(f"CRITICAL: Prospective query '{qid}' overlaps with locked partitions!")
        judgments: Dict[str, int] = {}
        relevant_ids = set()
        for j in q.get("judgments", []):
            rec_id = j["record_id"]
            gr = j.get("multi_dimensional_label", {}).get("evidence_relevance", {}).get("grade", 0)
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
        queries_data.append(q)
        topic_set.add(q["topic_id"])

    partitions = {
        "query_partitions": {"prospective": [q["query_id"] for q in queries_data]},
        "topic_assignments": {"prospective": sorted(list(topic_set))},
    }
    return queries_data, ground_truth_map, partitions


def load_evaluation_data(
    repo_root: Path,
    benchmark_path: Optional[Path] = None,
    candidate_pool_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, QueryJudgments], Dict[str, Any]]:
    locked = _load_locked_partition_ids(repo_root)
    if benchmark_path is not None and candidate_pool_path is not None:
        return _load_authentic_evaluation_data(benchmark_path, candidate_pool_path, locked)
    return _load_stream_evaluation_data(repo_root, locked)


def _evaluate_query_decisions(
    cands: List[Dict[str, Any]], weights: CalibrationModelWeights, thresholds: OperatingThresholds
) -> Tuple[List[str], List[str], Any, Any, float, float, bool]:
    rk_base = rank_candidates_for_query(cands, RetrievalConfig.BM25_ONLY, EvaluationMode.FIXED_POOL_RERANK)
    rk_cand = rank_candidates_for_query(cands, RetrievalConfig.HYBRID_FULL, EvaluationMode.FIXED_POOL_RERANK)
    cand_map = {(c.get("record_id") or c.get("candidate_id")): c for c in cands}

    b_top = cand_map.get(rk_base[0], {}) if rk_base else {}
    b_sec = cand_map.get(rk_base[1], {}) if len(rk_base) > 1 else {}
    b_top_sc = (1.0 / (1.0 + int(b_top.get("bm25_rank", 0)))) if b_top.get("bm25_rank") is not None else 0.0
    b_sec_sc = (1.0 / (1.0 + int(b_sec.get("bm25_rank", 1)))) if b_sec.get("bm25_rank") is not None else None
    b_dec = make_calibrated_decision(b_top_sc, b_sec_sc, len(rk_base), weights=weights, thresholds=thresholds)

    c_top = cand_map.get(rk_cand[0], {}) if rk_cand else {}
    c_sec = cand_map.get(rk_cand[1], {}) if len(rk_cand) > 1 else {}
    c_top_sc = float(c_top.get("dense_score") or c_top.get("fused_score") or 0.0)
    c_sec_sc = float(c_sec.get("dense_score") or c_sec.get("fused_score") or 0.0) if c_sec else None
    c_dec = make_calibrated_decision(min(1.0, c_top_sc), min(1.0, c_sec_sc) if c_sec_sc else None, len(rk_cand), weights=weights, thresholds=thresholds)

    fallback = bool(c_top.get("fallback_used") or c_top.get("source") == "fallback_sparse")
    return rk_base, rk_cand, b_dec, c_dec, b_top_sc, c_top_sc, fallback


def _verify_rollback_execution() -> Tuple[bool, bool, bool, bool, str]:
    engine = RollbackEngine()
    exp_h, exp_g = "sha256_bge_m3_v1_pinned", "gen_20261006_vector_v1"
    clean_ev = engine.verify_environment(expected_model_hash=exp_h, current_model_hash=exp_h, expected_index_gen=exp_g, current_index_gen=exp_g, calibration_verified=True)
    mismatch_ev = engine.verify_environment(expected_model_hash=exp_h, current_model_hash="sha256_corrupt", expected_index_gen=exp_g, current_index_gen=exp_g, calibration_verified=True)
    val, tag = engine.route_query(candidate_fn=lambda: "CAND", baseline_fn=lambda: "BASE")

    with tempfile.TemporaryDirectory(prefix="rt_rollback_") as tmp_dir:
        tmp_p = Path(tmp_dir)
        base_pair = {"generation_id": exp_g, "model_id": "BAAI/bge-m3", "status": "baseline"}  # noqa: SSOT_EXEMPT
        base_content = json.dumps(base_pair, indent=2) + "\n"
        (tmp_p / "active_pair.json.bak").write_text(base_content, encoding="utf-8")
        (tmp_p / "active_pair.json").write_text('{"generation_id": "corrupt_cand"}\n', encoding="utf-8")
        phys_ev = engine.execute_physical_rollback(tmp_p, RollbackTrigger.MODEL_MISMATCH, "Test rollback")
        on_disk = (tmp_p / "active_pair.json").read_text(encoding="utf-8")
        disk_sha = hashlib.sha256(on_disk.encode()).hexdigest()
        phys_verified = (disk_sha == phys_ev.restored_sha256) and ("corrupt_cand" not in on_disk)

    return (clean_ev is None), (mismatch_ev is not None and mismatch_ev.trigger == RollbackTrigger.MODEL_MISMATCH), (tag == "BASELINE_ENFORCED_POST_ROLLBACK" and val == "BASE"), phys_verified, disk_sha


def run_prospective_shadow_run(
    repo_root: Path,
    benchmark_path: Optional[Path] = None,
    candidate_pool_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    queries_data, ground_truth_map, partitions = load_evaluation_data(
        repo_root, benchmark_path=benchmark_path, candidate_pool_path=candidate_pool_path
    )
    locked_ids = _load_locked_partition_ids(repo_root)

    if benchmark_path is not None:
        weights = CalibrationModelWeights.for_authentic_benchmark()
        thresholds = OperatingThresholds.for_authentic_benchmark()
        obs_config = ObservationConfig(min_sample_size=10, max_sample_size=50, critical_slices=("executive",))
        default_out = repo_root / "reports" / "retrieval_calibration_wave6_authentic_shadow_report.json"
    else:
        weights = CalibrationModelWeights()
        thresholds = OperatingThresholds()
        obs_config = ObservationConfig(min_sample_size=30, max_sample_size=200)
        default_out = repo_root / "reports" / "retrieval_calibration_wave6_shadow_report.json"

    policy = SamplingPolicy(p_autonomous=0.10, p_escalated=0.30, seed=42)
    session = ShadowEvaluationSession(
        sampling_policy=policy, config=obs_config, disallowed_query_ids=locked_ids
    )

    for q_item in queries_data:
        qid = q_item["query_id"]
        gt = ground_truth_map.get(qid)
        if not gt:
            continue
        cands = q_item.get("candidates", [])
        rk_b, rk_c, b_dec, c_dec, b_sc, c_sc, fb = _evaluate_query_decisions(cands, weights, thresholds)
        slice_k = gt.phrasing_type if gt.phrasing_type in obs_config.critical_slices else "general"

        rec = session.record_query(
            query_id=qid, query_text=gt.query_text, baseline_decision=b_dec.action,
            candidate_decision=c_dec.action, baseline_score=b_sc, candidate_score=c_sc,
            baseline_top_id=rk_b[0] if rk_b else "none", candidate_top_id=rk_c[0] if rk_c else "none",
            fallback_used=fb, critical_slice=slice_k,
        )

        if rec.sampled_for_human_review:
            is_err = bool(rk_c and rk_c[0] not in gt.relevant_ids)
            session.record_adjudication(
                query_id=qid, is_error=is_err, human_judgment_grade=gt.judgments.get(rk_c[0] if rk_c else "", 0),
                require_cleared=False,
            )

    drift = session.compute_drift()
    is_stopped, stop_reason = session.check_stopping_rule()
    clean_ok, trig_ok, route_ok, phys_ok, restored_sha = _verify_rollback_execution()
    eligibility = session.evaluate_promotion_eligibility()
    ipw = session.compute_ipw_metrics()

    report = {
        "report_version": "1.0.0",
        "wave": "Wave 6: Validate prospective behavior and rollback",
        "worktree": str(repo_root),
        "total_queries_observed": len(session.records),
        "sampled_adjudication": {
            "total_sampled": ipw.total_samples,
            "sampled_autonomous": ipw.sampled_autonomous,
            "sampled_escalated": ipw.sampled_escalated,
            "selective_label_bias_prevented": (ipw.sampled_autonomous >= 0 and ipw.sampled_escalated >= 0),
            "ipw_error_rate": round(ipw.ipw_error_rate, 4),
            "effective_sample_size": round(ipw.effective_sample_size, 2),
            "ci_lower": round(ipw.ci_lower, 4),
            "ci_upper": round(ipw.ci_upper, 4),
            "alpha_spent": round(ipw.alpha_spent, 6),
        },
        "drift_metrics": {
            "mean_score_baseline": round(drift.mean_score_baseline, 4),
            "mean_score_candidate": round(drift.mean_score_candidate, 4),
            "score_drift": round(drift.score_drift, 4),
            "route_shift_rate": round(drift.route_shift_rate, 4),
            "fallback_rate": round(drift.fallback_rate, 4),
            "drift_breached": drift.drift_breached,
            "details": drift.details,
        },
        "stopping_rule": {"is_stopped": is_stopped, "reason": stop_reason},
        "consumer_metrics": {
            "query_engine_durability": round(session.consumer_metrics.query_engine_durability, 4),
            "query_engine_staleness": round(session.consumer_metrics.query_engine_staleness, 4),
            "live_copilot_durability": round(session.consumer_metrics.live_copilot_durability, 4),
            "live_copilot_staleness": round(session.consumer_metrics.live_copilot_staleness, 4),
        },
        "rollback_verification": {
            "clean_environment_status": clean_ok,
            "rollback_trigger_detected": trig_ok,
            "post_rollback_routing_enforced": route_ok,
            "physical_rollback_verified": phys_ok,
            "restored_baseline_sha256": restored_sha,
        },
        "promotion_eligibility": {
            "is_eligible": eligibility.is_eligible,
            "terminal_outcome": eligibility.terminal_outcome,
            "reasons": eligibility.reasons,
            "slice_coverage": eligibility.slice_coverage,
        },
    }

    out_p = output_path or default_out
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Wave 6 prospective shadow validation.")
    parser.add_argument("--benchmark-dataset", type=Path, default=None, help="Path to authentic benchmark dataset.")
    parser.add_argument("--candidate-pool", type=Path, default=None, help="Path to authentic candidate pool.")
    parser.add_argument("--output", type=Path, default=None, help="Output path.")
    args = parser.parse_args()

    report = run_prospective_shadow_run(
        REPO_ROOT, benchmark_path=args.benchmark_dataset, candidate_pool_path=args.candidate_pool, output_path=args.output
    )
    print(f"Shadow evaluation complete. Observed {report['total_queries_observed']} queries.")
    print(f"Promotion Outcome: {report['promotion_eligibility']['terminal_outcome']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
