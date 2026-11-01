"""11-gate BGE-M3 retrieval qualification evaluation and sidecar inspection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.graph_delta import _sidecar_counts

CANONICAL_11_GATES = (
    "real_bge_m3",
    "pair_binding",
    "coverage_complete",
    "core_thresholds",
    "delta_thresholds",
    "critical_cases",
    "rank_stability",
    "repeat_stability",
    "forbidden_leakage",
    "latency",
    "bm25_execution",
)


def _resolve_evaluation_paths(r_root: Path, explicit_path: str = "") -> List[Path]:
    candidate_eval_paths: List[Path] = []
    if explicit_path:
        candidate_eval_paths.append(Path(explicit_path))
    eval_dir = r_root / "retrieval_evaluations"
    if eval_dir.is_dir():
        candidate_eval_paths.extend(
            sorted(eval_dir.glob("retrieval-release-evaluation-*.json"), reverse=True)
        )
    return candidate_eval_paths


def _load_sealed_evaluation(
    candidate_eval_paths: List[Path],
    active_pair_data: Optional[Dict[str, Any]] = None,
    explicit_receipt: bool = False,
    synthetic_allowed: bool = False,
) -> tuple[Optional[Dict[str, Any]], str]:
    if not explicit_receipt and not active_pair_data:
        return None, "live_inspection"
    for p in candidate_eval_paths:
        if p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    continue
                if not (
                    loaded.get("contract_id")
                    in (
                        "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
                        "urn:nee:dual-pipeline:retrieval-release-evaluation:2.0.0",
                    )
                    or loaded.get("evaluation_contract")
                    == "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
                    or "gates" in loaded
                ):
                    continue

                # Status check: only passed evaluations qualify
                status = loaded.get("status")
                if status and status != "PASS":
                    continue

                cand = loaded.get("candidate_pair") or loaded.get("active_pair") or {}
                if not synthetic_allowed and (
                    cand.get("production_qualified") is False
                    or "dev" in str(cand.get("embedding_backend", "")).lower()
                ):
                    continue

                # Verify against active pair if available
                if active_pair_data:
                    act_graph = active_pair_data.get("graph_sha256")
                    act_sidecar = active_pair_data.get("sidecar_sha256")
                    act_gen = active_pair_data.get("generation_id")

                    rec_graph = cand.get("graph_sha256") or loaded.get("graph_sha256")
                    rec_sidecar = cand.get("sidecar_sha256") or loaded.get("sidecar_sha256")
                    rec_gen = cand.get("generation_id") or loaded.get("generation_id")

                    if act_graph and rec_graph and act_graph != rec_graph:
                        continue
                    if act_sidecar and rec_sidecar and act_sidecar != rec_sidecar:
                        continue
                    if act_gen and rec_gen and act_gen != rec_gen:
                        continue
                    if (act_graph or act_sidecar) and not (rec_graph or rec_sidecar):
                        continue

                return loaded, str(p)
            except Exception:
                continue
    return None, "live_inspection"


def _evaluate_live_gates(
    graph_path: Path,
    sidecar_path: Path,
    synthetic_allowed: bool,
) -> Dict[str, Dict[str, Any]]:
    s_counts = _sidecar_counts(sidecar_path) if sidecar_path.is_file() else {}
    dense_count = s_counts.get("dense_vector", 0)
    sparse_count = s_counts.get("sparse_weight", 0)
    colbert_count = s_counts.get("colbert_token", 0)
    record_count = s_counts.get("retrieval_record", 0)

    dense_dim_valid = dense_count > 0 or not sidecar_path.is_file()
    sparse_valid = sparse_count > 0 or not sidecar_path.is_file()
    colbert_valid = colbert_count > 0 or not sidecar_path.is_file()

    coverage_passed = (
        (dense_count >= record_count and sparse_count >= record_count)
        if record_count > 0
        else (dense_dim_valid and sparse_valid and colbert_valid)
    )

    gates: Dict[str, Dict[str, Any]] = {
        "real_bge_m3": {
            "passed": not synthetic_allowed and dense_dim_valid,
            "metric": "real_bge_m3_required",
            "threshold": "synthetic_allowed == False and valid BGE-M3 vectors",
            "measured": True,
        },
        "pair_binding": {
            "passed": graph_path.is_file() and sidecar_path.is_file(),
            "metric": f"{graph_path.name} / {sidecar_path.name}",
            "threshold": "graph and sidecar exist and match pair bindings",
            "measured": True,
        },
        "coverage_complete": {
            "passed": coverage_passed,
            "metric": f"records={record_count}, dense={dense_count}, sparse={sparse_count}",
            "threshold": "100% representation coverage across retrieval records",
            "measured": True,
        },
        "latency": {
            "passed": True,
            "metric_ms": 0.0,
            "threshold": "< 7500ms P95",
            "measured": False,
        },
        "bm25_execution": {
            "passed": graph_path.is_file(),
            "metric": "fts5_search_document_indexed",
            "threshold": "FTS5 BM25 search index active",
            "measured": True,
        },
    }

    for g_name in (
        "core_thresholds",
        "delta_thresholds",
        "critical_cases",
        "rank_stability",
        "repeat_stability",
        "forbidden_leakage",
    ):
        gates[g_name] = {
            "passed": False,
            "metric": None,
            "threshold": "Recall@K=1.0, MRR=1.0, NDCG=1.0, zero regressions",
            "measured": False,
            "reason": "Requires executed retrieval release suite or sealed evaluation receipt",
        }

    return gates


def _build_legacy_aliases(gates: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, bool]]:
    return {
        "gate_01_dense_coverage": {"passed": gates["coverage_complete"]["passed"]},
        "gate_02_sparse_coverage": {"passed": gates["coverage_complete"]["passed"]},
        "gate_03_colbert_coverage": {"passed": gates["coverage_complete"]["passed"]},
        "gate_04_no_synthetic_embeddings": {"passed": gates["real_bge_m3"]["passed"]},
        "gate_05_precision_at_10": {"passed": gates["core_thresholds"]["passed"]},
        "gate_06_recall_at_10": {"passed": gates["core_thresholds"]["passed"]},
        "gate_07_mrr_at_10": {"passed": gates["core_thresholds"]["passed"]},
        "gate_08_ndcg_at_10": {"passed": gates["core_thresholds"]["passed"]},
        "gate_09_steady_state_latency": {"passed": gates["latency"]["passed"]},
        "gate_10_lexical_semantic_balance": {"passed": gates["bm25_execution"]["passed"]},
        "gate_11_active_pair_binding": {"passed": gates["pair_binding"]["passed"]},
    }


def eval_retrieval_11gates(
    runtime_root: str = "",
    synthetic_allowed: bool = False,
    evaluation_receipt_path: str = "",
    workspace_root: Optional[Path] = None,
) -> str:
    """Evaluate the active retrieval pair against all 11 non-compensating release gates.

    Inspects active runtime pair, sealed evaluation receipts, and sidecar databases
    to verify compliance with urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0.

    Args:
        runtime_root: Optional path to runtime root (defaults to active runtime).
        synthetic_allowed: Whether synthetic embeddings are permitted (must be False for production qualification).
        evaluation_receipt_path: Optional explicit path to sealed retrieval release evaluation artifact.
        workspace_root: Optional workspace root override.
    """
    try:
        w_root = workspace_root or Path(__file__).resolve().parents[2]
        r_root = Path(runtime_root) if runtime_root else w_root / ".local" / "dual_pipeline"
        active_pair_file = r_root / "active_pair.json"

        graph_path = w_root / "graph" / "nee_graph.sqlite"
        sidecar_path = w_root / "graph" / "nee_index.sqlite"

        active_pair_data = None
        if active_pair_file.is_file():
            try:
                active_pair_data = json.loads(active_pair_file.read_text(encoding="utf-8"))
                gp = Path(active_pair_data.get("graph_path", ""))
                ip = Path(active_pair_data.get("index_path", ""))
                if gp.is_file():
                    graph_path = gp
                elif (r_root / gp).is_file():
                    graph_path = r_root / gp
                elif (w_root / gp).is_file():
                    graph_path = w_root / gp

                if ip.is_file():
                    sidecar_path = ip
                elif (r_root / ip).is_file():
                    sidecar_path = r_root / ip
                elif (w_root / ip).is_file():
                    sidecar_path = w_root / ip
            except Exception:
                pass

        candidate_paths = _resolve_evaluation_paths(r_root, evaluation_receipt_path)
        sealed_eval, eval_source = _load_sealed_evaluation(
            candidate_paths,
            active_pair_data=active_pair_data,
            explicit_receipt=bool(evaluation_receipt_path),
            synthetic_allowed=synthetic_allowed,
        )

        if sealed_eval and "gates" in sealed_eval:
            raw_gates = sealed_eval["gates"]
            gates: Dict[str, Dict[str, Any]] = {}
            for gate_name in CANONICAL_11_GATES:
                val = raw_gates.get(gate_name)
                if val is None:
                    for k, v in raw_gates.items():
                        if gate_name in k:
                            val = v
                            break
                passed = bool(val) if val is not None else False
                gates[gate_name] = {
                    "passed": passed,
                    "measured": True,
                    "source": eval_source,
                    "threshold": "Canonical 11-gate threshold verified in sealed receipt",
                }
            aggregate = sealed_eval.get("aggregate", {})
        else:
            gates = _evaluate_live_gates(graph_path, sidecar_path, synthetic_allowed)
            aggregate = {}

        legacy_aliases = _build_legacy_aliases(gates)
        canonical_passed = (
            all(gates[g]["passed"] for g in CANONICAL_11_GATES)
            and (sealed_eval.get("status") == "PASS" if sealed_eval else False)
        )

        return json.dumps(
            {
                "status": "QUALIFIED" if canonical_passed else "REJECTED",
                "all_gates_passed": canonical_passed,
                "evaluation_source": eval_source,
                "runtime_root": str(r_root),
                "graph_path": str(graph_path),
                "sidecar_path": str(sidecar_path),
                "gates": gates,
                "legacy_aliases": legacy_aliases,
                "aggregate": aggregate,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"error": f"11-gate evaluation failed: {exc}"})
