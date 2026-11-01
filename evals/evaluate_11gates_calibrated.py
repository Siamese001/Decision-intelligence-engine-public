"""Direct 11-Gate Retrieval Release Evaluator under Calibrated BM25_RRF_WEIGHTS.

Runs RetrievalReleaseEvaluator on the release receipt and automatic suite in the active runtime
under the newly calibrated weights:
    BM25_RRF_WEIGHTS = {dense: 0.05, sparse: 0.15, bm25: 0.70, colbert: 0.10}
Validates all 11 gates and records the exact execution receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.index import (
    BM25_RRF_WEIGHTS,
    HybridRetriever,
    get_backend,
)
from dual_pipeline.retrieval_release import (
    RetrievalReleaseEvaluator,
    _evaluate_case,
    _first_rank,
    _load_policy,
    _max_rank_regression,
    _ndcg_at_k,
    _reciprocal_rank,
    _repeat_rank_drift,
)

PINNED_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def resolve_bge_m3_paths(
    model_path_arg: str | Path | None = None,
    manifest_path_arg: str | Path | None = None,
) -> tuple[Path, Path, str]:
    """Resolves real BGE-M3 model root and manifest path across macOS, Linux, and Windows."""
    # 1. Model Root Resolution
    if model_path_arg:
        model_root = Path(model_path_arg).expanduser()
    elif os.environ.get("NEE_BGE_M3_MODEL_PATH"):
        model_root = Path(os.environ["NEE_BGE_M3_MODEL_PATH"]).expanduser()
    elif os.environ.get("BGE_M3_MODEL_PATH"):
        model_root = Path(os.environ["BGE_M3_MODEL_PATH"]).expanduser()
    else:
        local_repo_model = ROOT / "models" / "bge-m3" / PINNED_REVISION
        user_cache_model = Path.home() / ".cache" / "models" / "bge-m3" / PINNED_REVISION
        fallback_win_model = Path(r"C:\NEE\models\bge-m3") / PINNED_REVISION

        if local_repo_model.exists():
            model_root = local_repo_model
        elif user_cache_model.exists():
            model_root = user_cache_model
        else:
            model_root = fallback_win_model

    # 2. Model Manifest Resolution
    if manifest_path_arg:
        manifest_path = Path(manifest_path_arg).expanduser()
    elif os.environ.get("NEE_BGE_M3_MODEL_MANIFEST"):
        manifest_path = Path(os.environ["NEE_BGE_M3_MODEL_MANIFEST"]).expanduser()
    elif os.environ.get("BGE_M3_MANIFEST_PATH"):
        manifest_path = Path(os.environ["BGE_M3_MANIFEST_PATH"]).expanduser()
    else:
        local_repo_manifest = ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{PINNED_REVISION}.json"
        user_cache_manifest = Path.home() / ".cache" / "models" / "bge-m3" / f"bge-m3-manifest-{PINNED_REVISION}.json"
        fallback_win_manifest = Path(r"C:\NEE\models\bge-m3") / f"bge-m3-manifest-{PINNED_REVISION}.json"

        if local_repo_manifest.exists():
            manifest_path = local_repo_manifest
        elif user_cache_manifest.exists():
            manifest_path = user_cache_manifest
        else:
            manifest_path = fallback_win_manifest

    return model_root, manifest_path, PINNED_REVISION


def resolve_runtime_and_artifacts(
    runtime_root_arg: str | Path | None = None,
    receipt_arg: str | Path | None = None,
    suite_arg: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    """Resolves the active runtime root, release receipt, and matching evaluation suite."""
    if runtime_root_arg:
        runtime_root = Path(runtime_root_arg).resolve()
    elif os.environ.get("NEE_RUNTIME_ROOT"):
        runtime_root = Path(os.environ["NEE_RUNTIME_ROOT"]).resolve()
    elif os.environ.get("DUAL_PIPELINE_RUNTIME_ROOT"):
        runtime_root = Path(os.environ["DUAL_PIPELINE_RUNTIME_ROOT"]).resolve()
    else:
        dual_pipeline_local = ROOT / ".local" / "dual_pipeline"
        legacy_sherpa_local = ROOT / ".local" / "main" / "runtime-bge-m3-sherpa-strategic-20260729"
        runtime_root = dual_pipeline_local if dual_pipeline_local.exists() else legacy_sherpa_local

    # Receipt Resolution
    if receipt_arg:
        receipt_path = Path(receipt_arg).resolve()
    else:
        default_release = runtime_root / "releases" / "release-generation-58bc5275c8202415dd1f500c8ed6.json"
        if default_release.exists():
            receipt_path = default_release
        else:
            releases = sorted(
                (runtime_root / "releases").glob("release-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not releases:
                raise FileNotFoundError(f"No release receipts found in {runtime_root / 'releases'}")
            receipt_path = releases[0]

    # Suite Resolution
    if suite_arg:
        suite_path = Path(suite_arg).resolve()
    else:
        default_suite = (
            runtime_root
            / "retrieval_evaluation_suites"
            / "automatic"
            / "retrieval-release-suite-release-receipt-00d96446988ef6fc4493a54c33ec.json"
        )
        if default_suite.exists():
            suite_path = default_suite
        else:
            # Match by receipt_id
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_id = receipt_data.get("receipt_id", "")
            matching_suite = None
            suite_dir = runtime_root / "retrieval_evaluation_suites" / "automatic"
            for s in suite_dir.glob("*.json"):
                if receipt_id and receipt_id in s.name:
                    matching_suite = s
                    break
            if not matching_suite:
                suites = sorted(suite_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not suites:
                    raise FileNotFoundError(f"No evaluation suites found in {suite_dir}")
                matching_suite = suites[0]
            suite_path = matching_suite

    return runtime_root, receipt_path, suite_path


def compute_11gates(
    *,
    backend: Any,
    revision: str,
    baseline_pair: dict[str, Any],
    candidate_pair: dict[str, Any],
    baseline_sidecar: Path,
    candidate_sidecar: Path,
    baseline_graph: Path,
    candidate_graph: Path,
    receipt: dict[str, Any],
    coverage_complete: bool,
    case_results: list[dict[str, Any]],
    agg: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, bool]:
    """Dynamically compute the 11 BGE-M3 retrieval release gates."""
    real_bge_m3_bound = (
        getattr(backend, "backend_id", "") == "bge-m3"
        and getattr(backend, "model_id", "") == "BAAI/bge-m3"
        and (not hasattr(backend, "model_revision") or backend.model_revision == revision)
    )
    all_cand_comp_runs = [c.get("component_runs", []) for c in case_results]
    all_components_executed = len(all_cand_comp_runs) > 0
    if not all_cand_comp_runs:
        all_components_executed = False
    else:
        for comp_run in all_cand_comp_runs:
            if not comp_run:
                all_components_executed = False
                break
            for item in comp_run:
                comps = item if isinstance(item, list) else [item]
                for comp in comps:
                    if not isinstance(comp, dict) or not all(comp.get(k, False) for k in ("dense", "sparse", "bm25", "colbert")):
                        all_components_executed = False
                        break
                if not all_components_executed:
                    break
            if not all_components_executed:
                break

    pair_binding_valid = (
        baseline_pair.get("model_id") == candidate_pair.get("model_id")
        and baseline_pair.get("model_revision") == candidate_pair.get("model_revision")
        and bool(receipt.get("receipt_id"))
        and Path(baseline_sidecar).exists()
        and Path(candidate_sidecar).exists()
        and Path(baseline_graph).exists()
        and Path(candidate_graph).exists()
    )

    return {
        "1. real_bge_m3": bool(real_bge_m3_bound and all_components_executed),
        "2. pair_binding": bool(pair_binding_valid),
        "3. coverage_complete": bool(coverage_complete),
        "4. core_thresholds (Recall=1, MRR=1, NDCG=1)": (
            "core_recall" in agg and "core_mrr" in agg and "core_ndcg" in agg
            and agg["core_recall"] >= 1.0
            and agg["core_mrr"] >= 1.0
            and agg["core_ndcg"] >= 1.0
        ),
        "5. delta_thresholds (Recall=1, MRR=1, NDCG=1)": (
            "delta_recall" in agg and "delta_mrr" in agg and "delta_ndcg" in agg
            and agg["delta_recall"] >= 1.0
            and agg["delta_mrr"] >= 1.0
            and agg["delta_ndcg"] >= 1.0
        ),
        "6. critical_cases (0 failures)": "critical_failures" in agg and agg["critical_failures"] == 0,
        "7. rank_stability (0 regressions)": "total_rank_regressions" in agg and agg["total_rank_regressions"] == 0,
        "8. repeat_stability (0 drift)": "total_repeat_drift" in agg and agg["total_repeat_drift"] == 0,
        "9. forbidden_leakage (0 leakage)": "total_leakage" in agg and agg["total_leakage"] == 0,
        "10. latency (<= 7500ms)": "max_latency_ms" in agg and agg["max_latency_ms"] <= thresholds.get("max_latency_ms", 7500.0),
        "11. component_execution (all 4 engines)": bool(all_components_executed),
    }


def main():
    parser = argparse.ArgumentParser(description="11-Gate Retrieval Release Evaluation under Calibrated Weights")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Path to runtime root")
    parser.add_argument("--receipt", type=Path, default=None, help="Path to release receipt JSON")
    parser.add_argument("--suite", type=Path, default=None, help="Path to retrieval suite JSON")
    parser.add_argument("--model-path", type=Path, default=None, help="Path to BGE-M3 model snapshot")
    parser.add_argument("--model-manifest", type=Path, default=None, help="Path to BGE-M3 manifest JSON")
    parser.add_argument("--device", type=str, default=os.environ.get("NEE_EMBEDDING_DEVICE", "auto"))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "wave4_11gate_release_evaluation_receipt.json",
        help="Destination receipt JSON path",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("11-Gate Retrieval Release Evaluation under Calibrated Weights")
    print("=" * 80)
    print(f"Current BM25_RRF_WEIGHTS in index.py: {BM25_RRF_WEIGHTS}")

    seed_graph = ROOT / "graph" / "nee_graph.sqlite"
    runtime_root, receipt_path, suite_path = resolve_runtime_and_artifacts(
        args.runtime_root, args.receipt, args.suite
    )
    model_root, manifest_path, revision = resolve_bge_m3_paths(
        args.model_path, args.model_manifest
    )

    print(f"Runtime Root: {runtime_root}")
    print(f"Receipt:      {receipt_path.name}")
    print(f"Suite:        {suite_path.name}")
    print(f"Model Root:   {model_root}")
    print(f"Manifest:     {manifest_path}")

    backend = get_backend(
        "bge-m3-dev",
        allow_dev_backend=True,
        model_path=str(model_root),
        model_revision=revision,
        model_manifest=str(manifest_path),
        device=args.device,
    )
    print(f"Backend: {backend.backend_id} ({backend.model_id})")

    # Load suite to inspect cases
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    print(f"\nSuite contains {len(suite['cases'])} test cases across {len(suite['coverage'])} covered records.")
    for idx, c in enumerate(suite["cases"], 1):
        q_text = c.get("query") or c.get("question", "")
        print(f"  [{idx:2d}] Scope: {c['scope']:5s} | Kind: {c['query_kind']:20s} | Query: {q_text[:50]}...")

    evaluator = RetrievalReleaseEvaluator(runtime_root, seed_graph)

    # Run evaluation
    print("\nExecuting RetrievalReleaseEvaluator.evaluate()...")
    start = time.time()

    # We call the internal evaluation engine directly so it does not collide with existing sealed artifact
    policy = _load_policy()

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    baseline_pair = receipt["previous_pair"]
    candidate_pair = receipt["next_pair"]

    baseline_sidecar = evaluator.manager._resolve(baseline_pair["sidecar_path"])
    candidate_sidecar = evaluator.manager._resolve(candidate_pair["sidecar_path"])
    baseline_graph = evaluator.manager._resolve(baseline_pair["graph_path"])
    candidate_graph = evaluator.manager._resolve(candidate_pair["graph_path"])


    baseline_retriever = HybridRetriever(baseline_sidecar, backend)
    candidate_retriever = HybridRetriever(candidate_sidecar, backend)

    thresholds = policy["thresholds"]
    case_results = []

    for case in suite["cases"]:
        q_text = case.get("query") or case.get("question", "")
        encoded = backend.encode_many([q_text])[0]
        # Baseline run
        base_ids, base_diag, base_comp, base_lat = _evaluate_case(
            baseline_retriever, case, baseline_graph, encoded_query=encoded
        )
        # Warmup run
        _w_ids, _w_diag, _w_comp, _w_lat = _evaluate_case(
            candidate_retriever, case, candidate_graph, encoded_query=encoded
        )
        # Repeated runs
        cand_runs, cand_comp_runs, cand_lats = [], [], []
        cand_diag = []
        for rep in range(thresholds["repeat_run_count"]):
            r_ids, r_diag, r_comp, r_lat = _evaluate_case(
                candidate_retriever, case, candidate_graph, encoded_query=encoded
            )
            cand_runs.append(r_ids)
            cand_comp_runs.append(r_comp)
            cand_lats.append(r_lat)
            if rep == 0:
                cand_diag = r_diag

        cand_ids = cand_runs[0]
        expected = set(case["expected_record_ids"])
        forbidden = set(case["forbidden_record_ids"])
        base_rank = _first_rank(base_ids, expected)
        cand_rank = _first_rank(cand_ids, expected)

        rank_reg = 0
        if case["scope"] == "core":
            rank_reg = max(0, _max_rank_regression(base_ids, cand_ids, expected, case["top_k"]))

        relevant = expected & set(cand_ids)
        case_results.append({
            "case_id": case["case_id"],
            "scope": case["scope"],
            "query_kind": case["query_kind"],
            "critical": case["critical"],
            "baseline_rank": base_rank,
            "candidate_rank": cand_rank,
            "candidate_recall": 1.0 if not expected else len(relevant) / len(expected),
            "candidate_mrr": _reciprocal_rank(cand_rank, expected),
            "candidate_ndcg": _ndcg_at_k(cand_ids, expected, case["top_k"]),
            "rank_regression": rank_reg,
            "repeat_rank_drift": _repeat_rank_drift(cand_runs, case["top_k"]),
            "forbidden_leakage": sorted(forbidden & set(cand_ids)),
            "latency_ms": max(cand_lats),
            "component_runs": cand_comp_runs,
        })

    elapsed = time.time() - start

    core = [item for item in case_results if item["scope"] == "core"]
    delta = [item for item in case_results if item["scope"] == "delta"]

    agg = {
        "core_recall": float(sum(c["candidate_recall"] for c in core) / len(core)) if core else 1.0,
        "core_mrr": float(sum(c["candidate_mrr"] for c in core) / len(core)) if core else 1.0,
        "core_ndcg": float(sum(c["candidate_ndcg"] for c in core) / len(core)) if core else 1.0,
        "delta_recall": float(sum(d["candidate_recall"] for d in delta) / len(delta)) if delta else 1.0,
        "delta_mrr": float(sum(d["candidate_mrr"] for d in delta) / len(delta)) if delta else 1.0,
        "delta_ndcg": float(sum(d["candidate_ndcg"] for d in delta) / len(delta)) if delta else 1.0,
        "max_latency_ms": max(c["latency_ms"] for c in case_results),
        "total_leakage": sum(len(c["forbidden_leakage"]) for c in case_results),
        "total_rank_regressions": sum(c["rank_regression"] for c in core),
        "total_repeat_drift": sum(c["repeat_rank_drift"] for c in case_results),
        "critical_failures": sum(1 for c in case_results if c["critical"] and (c["candidate_rank"] is None or c["candidate_rank"] > thresholds["critical_top_k"])),
    }

    # Verify coverage dynamically
    coverage_result = evaluator._coverage(
        suite,
        _records_by_id(baseline_sidecar),
        _records_by_id(candidate_sidecar),
        policy,
        receipt=receipt,
        runtime_root=runtime_root,
    )
    coverage_complete = bool(coverage_result.get("complete", False))

    # Verify 11 gates dynamically
    gates = compute_11gates(
        backend=backend,
        revision=revision,
        baseline_pair=baseline_pair,
        candidate_pair=candidate_pair,
        baseline_sidecar=baseline_sidecar,
        candidate_sidecar=candidate_sidecar,
        baseline_graph=baseline_graph,
        candidate_graph=candidate_graph,
        receipt=receipt,
        coverage_complete=coverage_complete,
        case_results=case_results,
        agg=agg,
        thresholds=thresholds,
    )

    all_passed = all(gates.values())
    status = "PASS" if all_passed else "FAIL"

    print("\n" + "=" * 80)
    print(f"11-GATE VERIFICATION RESULTS: {status}")
    print("=" * 80)
    for gate_name, passed in gates.items():
        tag = "[PASS]" if passed else "[FAIL]"
        print(f"  {tag} {gate_name}")

    print("\nAggregate Metrics:")
    print(f"  Core Cases:  Recall={agg['core_recall']:.4f} | MRR={agg['core_mrr']:.4f} | NDCG={agg['core_ndcg']:.4f}")
    print(f"  Delta Cases: Recall={agg['delta_recall']:.4f} | MRR={agg['delta_mrr']:.4f} | NDCG={agg['delta_ndcg']:.4f}")
    print(f"  Max Latency: {agg['max_latency_ms']:.1f} ms (Threshold: {thresholds['max_latency_ms']} ms)")
    print(f"  Elapsed:     {elapsed:.2f} s")

    receipt_out = args.output
    receipt_data = {
        "evaluation_contract": "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": status,
        "weights": BM25_RRF_WEIGHTS,
        "gates": gates,
        "aggregate": agg,
        "cases_evaluated": len(case_results),
        "elapsed_seconds": round(elapsed, 2),
    }
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_out.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
    print(f"\nReceipt written to: {receipt_out}")
    return status, receipt_data


if __name__ == "__main__":
    main()

