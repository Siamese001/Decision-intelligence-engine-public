"""Agent-C4: 11-Gate Release Verification Auditor Agent.

Verifies the 11 non-compensating retrieval release gates under real BGE-M3 execution
prior to any activation snapshot swap via active_pair.json.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.parameters import get_float_parameter

logger = logging.getLogger("dual_pipeline.agents.release_gate_auditor")

GATE_NAMES = (
    "real_bge_m3",
    "pair_binding",
    "coverage_complete",
    "core_thresholds",
    "delta_thresholds",
    "critical_cases",
    "rank_stability",
    "repeat_stability",
    "forbidden_leakage",
    "latency_threshold",
    "component_execution",
)


def get_release_gate_auditor_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:release-gate-auditor:1.0.0",
        name="ReleaseGateAuditor",
        version="1.0.0",
        role="Retrieval Qualification Officer",
        domain_focus="Audit and verify the 11 non-compensating BGE-M3 release gates",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id="deterministic",
            provider="bge",
            temperature=0.0,
        ),
        skills=["Verify11Gates", "ValidateActivePairBinding", "AuditLeakageDefense"],
        allowed_tools=["retrieval_eval_harness"],
    )


def _validate_backend_name(backend: str, mock_mode: bool, model_id: Any) -> Optional[str]:
    if backend == "bge-m3-dev" or "dev" in backend:
        return "Dev backend 'bge-m3-dev' rejected; production qualification requires 'bge-m3'"
    if "synthetic" in backend:
        return "Synthetic embeddings prohibited in production BGE-M3 release qualification"
    if not mock_mode:
        if "mock" in backend:
            return f"Model {model_id} is not verified real BGE-M3 backend"
        if backend != "bge-m3":
            return f"Backend {backend} is not verified production BGE-M3"
    return None


def _eval_real_bge_m3(payload: Dict[str, Any], mock_mode: bool, graph_sha: str, strict: bool = False) -> tuple[bool, str]:
    backend_val = payload.get("backend_id") or payload.get("embedding_backend") or payload.get("model_id")
    if not backend_val:
        return False, "Missing backend_id or embedding_backend in evaluation payload"
    backend = str(backend_val).lower()
    if payload.get("synthetic_allowed", False):
        return False, "Synthetic embeddings prohibited in production BGE-M3 release qualification"
    err = _validate_backend_name(backend, mock_mode, payload.get("model_id"))
    if err:
        return False, err
    if payload.get("real_bge_m3_passed") is False:
        return False, "BGE-M3 verification explicitly failed in evaluation payload"
    if strict and payload.get("real_bge_m3_passed") is not True:
        return False, "BGE-M3 verification status missing or incomplete in evaluation payload"
    if not mock_mode and ("mock" in graph_sha.lower() or not graph_sha):
        return False, "Real BGE-M3 model binding verification failed outside mock mode"
    return True, "Verified"


def _eval_pair_binding(
    payload: Dict[str, Any], mock_mode: bool, graph_sha: str, sidecar_sha: str, strict: bool = False
) -> tuple[bool, str]:
    if not graph_sha or not sidecar_sha:
        return False, "Missing graph or sidecar SHA-256 pair binding"
    if not mock_mode and ("mock" in graph_sha.lower() or "mock" in sidecar_sha.lower()):
        return False, "Mock SHA-256 pair bindings rejected outside mock mode"
    if payload.get("pair_binding_passed") is False:
        return False, "Pair binding verification failed"
    if strict and payload.get("pair_binding_passed") is not True:
        return False, "Pair binding verification missing or incomplete"
    return True, "Verified"


def _eval_coverage_complete(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if payload.get("coverage_complete") is False:
        return False, "Retrieval representation coverage is incomplete (< 100%)"
    if strict and payload.get("coverage_complete") is not True:
        return False, "Retrieval representation coverage status missing or incomplete"
    if "coverage_ratio" in payload and float(payload.get("coverage_ratio")) < 1.0:
        return False, "Retrieval representation coverage ratio is incomplete (< 100%)"
    if strict and "coverage_ratio" not in payload:
        return False, "Coverage ratio missing in evaluation payload"
    if "total_queries" in payload and int(payload.get("total_queries")) < 3:
        return False, "Insufficient evaluation query coverage (< 3 queries)"
    if strict and "total_queries" not in payload:
        return False, "Total queries missing in evaluation payload"
    return True, "Verified"


def _eval_core_thresholds(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    c_metrics = payload.get("core_metrics", {}) if isinstance(payload.get("core_metrics"), dict) else {}
    has_recall = "core_recall" in payload or "recall_at_10" in c_metrics or "recall" in c_metrics
    has_mrr = "core_mrr" in payload or "mrr" in c_metrics
    has_ndcg = "core_ndcg" in payload or "ndcg" in c_metrics
    if strict and (not has_recall or not has_mrr or not has_ndcg):
        return False, "Missing core metrics (recall, mrr, ndcg) in evaluation payload"
    core_recall = float(payload.get("core_recall", c_metrics.get("recall_at_10", c_metrics.get("recall", 1.0 if not strict else 0.0))))
    core_mrr = float(payload.get("core_mrr", c_metrics.get("mrr", 1.0 if not strict else 0.0)))
    core_ndcg = float(payload.get("core_ndcg", c_metrics.get("ndcg", 1.0 if not strict else 0.0)))
    if payload.get("core_thresholds_passed") is False or core_recall < 1.0 or core_mrr < 1.0 or core_ndcg < 1.0:
        return False, f"Core metrics below 1.0 non-compensating floor: recall={core_recall}, mrr={core_mrr}, ndcg={core_ndcg}"
    if strict and payload.get("core_thresholds_passed") is not True:
        return False, "Core thresholds verification missing or incomplete"
    return True, "Verified"


def _eval_delta_metrics(payload: Dict[str, Any], strict: bool) -> tuple[bool, str]:
    if strict and ("delta_recall" not in payload or "delta_mrr" not in payload or "delta_ndcg" not in payload):
        return False, "Missing delta metrics in evaluation payload"
    default_val = 0.0 if strict else 1.0
    delta_recall = float(payload.get("delta_recall", default_val))
    delta_mrr = float(payload.get("delta_mrr", default_val))
    delta_ndcg = float(payload.get("delta_ndcg", default_val))
    if delta_recall < 1.0 or delta_mrr < 1.0 or delta_ndcg < 1.0:
        return False, f"Delta metrics below 1.0 floor: recall={delta_recall}, mrr={delta_mrr}, ndcg={delta_ndcg}"
    return True, "Verified"


def _eval_delta_thresholds(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if strict and "has_delta" not in payload and "delta_thresholds_passed" not in payload:
        return False, "Missing delta verification fields (has_delta / delta_thresholds_passed) in evaluation payload"
    if payload.get("delta_thresholds_passed") is False:
        return False, "Delta thresholds verification failed"
    if bool(payload.get("has_delta", False)):
        return _eval_delta_metrics(payload, strict)
    return True, "Verified"


def _eval_critical_cases(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if strict and "critical_failure_count" not in payload and "critical_cases_passed" not in payload:
        return False, "Missing critical cases verification in evaluation payload"
    crit_failures = int(payload.get("critical_failure_count", 0 if not strict else 1))
    if payload.get("critical_cases_passed") is False or crit_failures > 0:
        return False, f"Critical retrieval cases failed (count={crit_failures})"
    return True, "Verified"


def _eval_rank_stability(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if strict and "rank_regression_count" not in payload and "rank_stability_passed" not in payload:
        return False, "Missing rank stability verification in evaluation payload"
    regressions = int(payload.get("rank_regression_count", 0 if not strict else 1))
    if payload.get("rank_stability_passed") is False or regressions > 0:
        return False, f"Baseline rank regressions detected (count={regressions})"
    return True, "Verified"


def _eval_repeat_stability(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if strict and "repeat_drift_count" not in payload and "repeat_stability_passed" not in payload:
        return False, "Missing repeat stability verification in evaluation payload"
    repeat_drift = int(payload.get("repeat_drift_count", 0 if not strict else 1))
    if payload.get("repeat_stability_passed") is False or repeat_drift > 0 or payload.get("repeat_stable") is False:
        return False, f"Repeat candidate runs exhibited rank drift (count={repeat_drift})"
    return True, "Verified"


def _eval_forbidden_leakage(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    if strict and "forbidden_leakage_count" not in payload and "forbidden_leakage_passed" not in payload:
        return False, "Missing forbidden leakage verification in evaluation payload"
    leak_count = int(payload.get("forbidden_leakage_count", 0 if not strict else 1))
    if payload.get("forbidden_leakage_passed") is False or leak_count > 0 or bool(payload.get("has_leakage")):
        return False, f"Forbidden record leakage detected (count={leak_count})"
    return True, "Verified"


def _eval_latency_threshold(payload: Dict[str, Any], max_query_latency_ms: float, strict: bool = False) -> tuple[bool, str]:
    max_latency_ceiling = get_float_parameter("timeouts.retrieval_steady_state_latency_ms", 7500.0)
    if strict and "max_query_latency_ms" not in payload:
        return False, "Missing max_query_latency_ms in evaluation payload"
    if max_query_latency_ms > max_latency_ceiling or payload.get("latency_passed") is False:
        return False, f"Max query latency {max_query_latency_ms}ms exceeds {max_latency_ceiling}ms ceiling"
    return True, "Verified"


def _eval_component_execution(payload: Dict[str, Any], strict: bool = False) -> tuple[bool, str]:
    components = payload.get("components")
    if strict and (components is None or "bm25_executed" not in payload):
        return False, "Missing component execution verification in evaluation payload"
    bm25_exec = payload.get("bm25_executed", True if not strict else False)
    if payload.get("component_execution_passed") is False or not bm25_exec or (components and not all(components.values())):
        return False, "One or more hybrid retrieval components (dense, sparse, bm25, colbert) failed execution"
    return True, "Verified"


class ReleaseGateAuditor:
    """Agent auditing all 11 release gates before activation."""

    def __init__(self, mock_mode: bool = False, strict_payload: bool | None = None) -> None:
        self.mock_mode = mock_mode
        self.strict = (not mock_mode) if strict_payload is None else strict_payload
        self.card = get_release_gate_auditor_card()

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Runs the 11 non-compensating release gates against candidate pair."""
        payload = task.input_payload or {}
        graph_sha = payload.get("graph_sha256", "mock-graph-sha" if self.mock_mode else "")
        sidecar_sha = payload.get("sidecar_sha256", "mock-sidecar-sha" if self.mock_mode else "")
        max_query_latency_ms = float(payload.get("max_query_latency_ms", 120.0 if not self.strict else float("inf")))

        logger.info(f"ReleaseGateAuditor verifying 11 release gates for pair ({graph_sha[:8]}..{sidecar_sha[:8]})")

        evaluators = {
            "real_bge_m3": lambda: _eval_real_bge_m3(payload, self.mock_mode, graph_sha, strict=self.strict),
            "pair_binding": lambda: _eval_pair_binding(payload, self.mock_mode, graph_sha, sidecar_sha, strict=self.strict),
            "coverage_complete": lambda: _eval_coverage_complete(payload, strict=self.strict),
            "core_thresholds": lambda: _eval_core_thresholds(payload, strict=self.strict),
            "delta_thresholds": lambda: _eval_delta_thresholds(payload, strict=self.strict),
            "critical_cases": lambda: _eval_critical_cases(payload, strict=self.strict),
            "rank_stability": lambda: _eval_rank_stability(payload, strict=self.strict),
            "repeat_stability": lambda: _eval_repeat_stability(payload, strict=self.strict),
            "forbidden_leakage": lambda: _eval_forbidden_leakage(payload, strict=self.strict),
            "latency_threshold": lambda: _eval_latency_threshold(payload, max_query_latency_ms, strict=self.strict),
            "component_execution": lambda: _eval_component_execution(payload, strict=self.strict),
        }

        gate_results: Dict[str, Dict[str, Any]] = {}
        all_passed = True

        for gate in GATE_NAMES:
            eval_fn = evaluators.get(gate)
            passed, reason = eval_fn() if eval_fn else (True, "Verified")
            gate_results[gate] = {"status": "PASS" if passed else "FAIL", "reason": reason}
            if not passed:
                all_passed = False

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "all_gates_passed": all_passed,
            "gate_results": gate_results,
            "passed_gate_count": len([g for g in gate_results.values() if g["status"] == "PASS"]),
            "total_gate_count": len(GATE_NAMES),
            "qualification_status": "DELIVERY_READY" if all_passed else "BLOCKED",
        }
