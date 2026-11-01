"""Pipelines 7 and 8 E2E scenario runners: Query Router/Retrieval/11-Gates and Decision API/A2A/HITL Learning."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from dual_pipeline.a2a.protocol import (
    A2ATask,
    A2ATaskStatus,
    ClaimCheckPointer,
    DelegationBudget,
)
from dual_pipeline.a2a.task_ledger import A2ATaskLedger
from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.decision_api import DecisionHTTPServer
from dual_pipeline.grounding import (
    ClaimManifest,
    ClaimRecord,
    GroundedDeliveryGuard,
    SourceLocator,
)
from dual_pipeline.hitl_governance import (
    compute_bayesian_calibrated_score,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.index import HybridRetriever
from dual_pipeline.parameters import needs_operator_decision
from dual_pipeline.query_router import route_query
from dual_pipeline.retrieval_release import _load_policy

from .live_and_graph_scenarios import DeterministicSandboxBgeBackend
from .models import ScenarioVerificationResult

PIPELINE_P7_ID = "P7_QUERY_ROUTER_RETRIEVAL_AND_GATES"
PIPELINE_P7_NAME = "Query Router, Hybrid Retrieval & 11-Gate Evaluation"
PIPELINE_P8_ID = "P8_DECISION_INTELLIGENCE_A2A_AND_LEARNING"
PIPELINE_P8_NAME = "Decision Intelligence API, A2A Ledger & HITL Learning"

ELEVEN_BGE_M3_GATE_NAMES: tuple[str, ...] = (
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


def evaluate_11_gate_matrix(metrics: dict[str, Any], thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate all 11 BGE-M3 retrieval release gates with non-compensating AND logic."""
    eff_thresholds = thresholds if thresholds is not None else _load_policy()["thresholds"]
    gates = {
        "real_bge_m3": bool(metrics.get("real_bge_m3", True)),
        "pair_binding": bool(metrics.get("pair_binding", True)),
        "coverage_complete": bool(metrics.get("coverage_complete", True)),
        "core_thresholds": (
            float(metrics.get("core_recall_at_k", 1.0)) >= float(eff_thresholds["min_core_recall_at_k"])
            and float(metrics.get("core_mrr", 1.0)) >= float(eff_thresholds["min_core_mrr"])
            and float(metrics.get("core_ndcg_at_k", 1.0)) >= float(eff_thresholds["min_core_ndcg_at_k"])
        ),
        "delta_thresholds": (
            float(metrics.get("delta_recall_at_k", 1.0)) >= float(eff_thresholds["min_delta_recall_at_k"])
            and float(metrics.get("delta_mrr", 1.0)) >= float(eff_thresholds["min_delta_mrr"])
            and float(metrics.get("delta_ndcg_at_k", 1.0)) >= float(eff_thresholds["min_delta_ndcg_at_k"])
        ),
        "critical_cases": int(metrics.get("critical_failure_count", 0)) == 0,
        "rank_stability": int(metrics.get("max_rank_regression", 0)) <= int(eff_thresholds["max_rank_regression"]),
        "repeat_stability": int(metrics.get("repeat_rank_drift", 0)) <= int(eff_thresholds["max_repeat_rank_drift"]),
        "forbidden_leakage": int(metrics.get("forbidden_leakage_count", 0)) <= int(eff_thresholds["max_forbidden_leakage_count"]),
        "latency": float(metrics.get("max_latency_ms", 100.0)) <= float(eff_thresholds["max_latency_ms"]),
        "bm25_execution": bool(metrics.get("bm25_execution", True)),
    }
    return {"gates": gates, "status": "PASS" if all(gates.values()) else "FAIL"}


def run_p7_query_router_and_retrieval_reachability_scenario(
    sandbox_root: Path, graph_path: Path, sidecar_path: Path
) -> ScenarioVerificationResult:
    """Verify 8-route query routing, hybrid retrieval reachability, and GroundedDeliveryGuard."""
    t0 = time.perf_counter()
    r_ev = route_query("What is the exact quote from source-e2e-01?", query_type_override="exact_lookup")
    r_di = route_query(
        "What are the cross-workstream risks and inconsistencies for Core Engine?",
        query_type_override="cross_workstream_synthesis",
    )
    canary_pairs = [
        ("Show exact evidence citations from the architecture review.", "exact_lookup"),
        ("What is the current project status for Core Engine?", "project_status_current"),
        ("Explain the relationship and dependencies between Core Engine and Hybrid Sidecar.", "relationship_explanation"),
    ]
    canary_routes = [route_query(q, query_type_override=qt).destination.value for q, qt in canary_pairs]
    routing_ok = (
        r_ev.destination.value == "GRAPH_EVIDENCE_UTILITY"
        and r_di.destination.value == "NEE_DECISION_INTELLIGENCE"
        and "ROUTE_AMBIGUOUS" not in canary_routes
    )

    ro_conn = connect_sqlite(graph_path, readonly=True)
    fts_hits = ro_conn.execute(
        """SELECT a.assertion_id FROM search_fts f
           JOIN search_document sd ON sd.search_rowid = f.rowid
           JOIN assertion a ON a.assertion_id = sd.assertion_id
           WHERE search_fts MATCH 'Synchronizes OR Sidecar'
             AND a.canonical_status='canonical' AND a.canonical_eligible=1"""
    ).fetchall()
    ro_conn.close()
    retriever = HybridRetriever(sidecar_path, DeterministicSandboxBgeBackend())
    hybrid_res = retriever.retrieve(
        query="Core Engine synchronizes with Hybrid Sidecar",
        allowed_lanes=["approved"],
        classification="internal",
        audiences=["client_approved_external", "internal_consulting"],
    )
    lane_items = hybrid_res.get("lanes", {}).get("approved", {}).get("results", [])
    hybrid_ids = [
        item.get("record", {}).get("extensions", {}).get("com.partner_advisory.nee.graph-object-id")
        for item in lane_items
        if isinstance(item, dict)
    ]
    reachability_ok = any(r[0] == "as-e2e-canonical-01" for r in fts_hits) and "as-e2e-canonical-01" in hybrid_ids

    g_sha = sha256_file(graph_path)
    (sandbox_root / "active_pair.json").write_text(
        json.dumps({"generation_id": "gen-sandbox-e2e-01", "graph_path": str(graph_path), "sidecar_path": str(sidecar_path), "graph_sha256": g_sha}),
        encoding="utf-8",
    )
    payload_text = "Core Engine synchronizes with Hybrid Sidecar for deterministic retrieval."
    claim = ClaimRecord(
        claim_id="clm-e2e-01", subject="Core Engine", predicate="SYNCHRONIZES_WITH",
        object="Hybrid Sidecar", scope="deterministic retrieval", negation=False,
        assertion_ids=["as-e2e-canonical-01"], evidence_lane="approved", epistemic_status="canonical",
        source_locators=[SourceLocator(locator_type="LINE", source_uri="source-e2e-01", locator_value="line:1")],
    )
    manifest = ClaimManifest(
        manifest_id="man-e2e-01", payload_digest=hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        runtime_root=str(sandbox_root), graph_generation_id="gen-sandbox-e2e-01", graph_sha256=g_sha, claims=[claim],
    )
    guard = GroundedDeliveryGuard()
    valid_delivery = guard.deliver(payload_text, manifest, sandbox_root, "QUERY_SYNTHESIS", lambda t: t, audience="CLIENT_SAFE")
    tampered_delivery = guard.deliver(payload_text + " Unverified claim!", manifest, sandbox_root, "QUERY_SYNTHESIS", lambda t: t, audience="CLIENT_SAFE")
    grounding_ok = bool(valid_delivery.delivered and not tampered_delivery.delivered and tampered_delivery.safe_response is not None)

    passed = bool(routing_ok and reachability_ok and grounding_ok)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P7-ROUTER-RETRIEVAL-AND-GROUNDING",
        pipeline_id=PIPELINE_P7_ID,
        pipeline_name=PIPELINE_P7_NAME,
        title="Query Router, Hybrid Retrieval Reachability & GroundedDeliveryGuard",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=["QUERY_ROUTER_DISAMBIGUATION", "POST_INGEST_HYBRID_REACHABILITY", "GROUNDED_DELIVERY_GUARD"],
        metrics={"fts_hit_count": len(fts_hits), "hybrid_ids": hybrid_ids, "valid_delivered": valid_delivery.delivered},
    )


def run_p7_bge_m3_11_gate_non_compensating_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify all 11 BGE-M3 retrieval gates pass and any single-gate perturbation forces FAIL."""
    t0 = time.perf_counter()
    base_metrics: dict[str, Any] = {
        "real_bge_m3": True, "pair_binding": True, "coverage_complete": True,
        "core_recall_at_k": 1.0, "core_mrr": 1.0, "core_ndcg_at_k": 1.0,
        "delta_recall_at_k": 1.0, "delta_mrr": 1.0, "delta_ndcg_at_k": 1.0,
        "critical_failure_count": 0, "max_rank_regression": 0, "repeat_rank_drift": 0,
        "forbidden_leakage_count": 0, "max_latency_ms": 420.0, "bm25_execution": True,
    }
    baseline_eval = evaluate_11_gate_matrix(base_metrics)
    perturbations: list[tuple[str, dict[str, Any]]] = [
        ("real_bge_m3", {"real_bge_m3": False}),
        ("pair_binding", {"pair_binding": False}),
        ("coverage_complete", {"coverage_complete": False}),
        ("core_thresholds", {"core_recall_at_k": 0.999}),
        ("delta_thresholds", {"delta_ndcg_at_k": 0.999}),
        ("critical_cases", {"critical_failure_count": 1}),
        ("rank_stability", {"max_rank_regression": 1}),
        ("repeat_stability", {"repeat_rank_drift": 1}),
        ("forbidden_leakage", {"forbidden_leakage_count": 1}),
        ("latency", {"max_latency_ms": 7500.001}),
        ("bm25_execution", {"bm25_execution": False}),
    ]
    non_comp_ok = True
    for gate_name, override in perturbations:
        res = evaluate_11_gate_matrix({**base_metrics, **override})
        if res["status"] != "FAIL" or res["gates"][gate_name] is not False:
            non_comp_ok = False
            break
    passed = bool(baseline_eval["status"] == "PASS" and len(baseline_eval["gates"]) == 11 and non_comp_ok)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P7-11-GATE-NON-COMPENSATING",
        pipeline_id=PIPELINE_P7_ID,
        pipeline_name=PIPELINE_P7_NAME,
        title="11-Gate BGE-M3 Non-Compensating Release Evaluation",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=["ALL_11_GATES_PASS", "SINGLE_GATE_PERTURBATION_FAILS_CLOSED"],
        metrics={"gate_count": len(baseline_eval["gates"]), "non_compensating_verified": non_comp_ok},
    )


def run_p8_decision_api_and_a2a_ledger_scenario(sandbox_root: Path, graph_path: Path) -> ScenarioVerificationResult:
    """Verify DecisionHTTPServer active_graph health target and A2ATaskLedger state transitions."""
    t0 = time.perf_counter()

    class _DummyDecisionService:
        runtime_root = sandbox_root

    srv = DecisionHTTPServer(("127.0.0.1", 0), _DummyDecisionService())
    try:
        resolved_db = srv._resolve_health_db_target()
        health_ok, health_payload = srv.deep_health_check()
    finally:
        srv.shutdown_gracefully()
    checks = health_payload.get("checks", {})
    api_ok = (
        Path(resolved_db).resolve() == graph_path.resolve()
        and health_ok
        and checks.get("sqlite") == "OK"
        and checks.get("sqlite_target") == "active_graph"
    )

    ledger_db = sandbox_root / "p8_a2a_ledger.sqlite"
    ledger = A2ATaskLedger(ledger_db)
    task = A2ATask(
        task_id="task-p8-01", parent_task_id=None, correlation_id="corr-p8-01",
        idempotency_key="idem-p8-01", sender_agent_id="orchestrator", recipient_agent_id="verifier",
        objective="Verify end-to-end pipeline scenario", status=A2ATaskStatus.SUBMITTED,
        scope_in=["dual_pipeline"], scope_out=["external"], allowed_tools=["read_file"],
        budget=DelegationBudget(max_turns=5, max_tool_calls=20, timeout_seconds=30.0, max_cost=0.5),
        claim_checks=[ClaimCheckPointer(source_id="source-e2e-01", evidence_id="ev-e2e-001", content_digest="a" * 64)],
        input_payload={"pipeline": "P8"},
    )
    ledger.record_task_submission(task)
    dup = ledger.record_task_submission(task)
    ledger.update_task_status("task-p8-01", A2ATaskStatus.ACCEPTED)
    ledger.update_task_status("task-p8-01", A2ATaskStatus.WORKING)
    completed = ledger.update_task_status("task-p8-01", A2ATaskStatus.COMPLETED, output_payload={"verdict": "PASS"})
    illegal_blocked = False
    try:
        ledger.update_task_status("task-p8-01", A2ATaskStatus.WORKING)
    except ValueError:
        illegal_blocked = True

    a2a_ok = bool(dup.task_id == "task-p8-01" and completed.status == A2ATaskStatus.COMPLETED and illegal_blocked)
    passed = bool(api_ok and a2a_ok)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P8-DECISION-API-AND-A2A-LEDGER",
        pipeline_id=PIPELINE_P8_ID,
        pipeline_name=PIPELINE_P8_NAME,
        title="DecisionHTTPServer Active-Graph Health & A2A Task Ledger State Machine",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"a2a_ledger_sqlite": str(ledger_db)},
        verified_invariants=["DECISION_API_ACTIVE_GRAPH_HEALTH", "A2A_IDEMPOTENCY", "A2A_STATE_MACHINE_GUARD"],
        metrics={"sqlite_target": checks.get("sqlite_target"), "illegal_transition_blocked": illegal_blocked},
    )


def run_p8_hitl_bayesian_learning_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify SSOT 20% ambiguity margin gating and bounded Beta-Binomial posterior calibration."""
    t0 = time.perf_counter()
    ambiguous = needs_operator_decision(0.75, 0.60)
    exact_boundary = needs_operator_decision(0.80, 0.60)
    decisive = needs_operator_decision(0.85, 0.50)

    low_clamped = compute_bayesian_calibrated_score(base_score=0.10, affirmed_weight=0.0, rejected_weight=50.0)
    high_clamped = compute_bayesian_calibrated_score(base_score=0.90, affirmed_weight=50.0, rejected_weight=0.0)
    boosted = compute_bayesian_calibrated_score(base_score=0.70, affirmed_weight=10.0, rejected_weight=1.0)
    gate_res = evaluate_hitl_surfacing_gate(
        options=[{"option_id": "opt1", "confidence": 0.90}, {"option_id": "opt2", "confidence": 0.55}]
    )
    passed = bool(
        ambiguous is True
        and exact_boundary is True
        and decisive is False
        and 0.05 <= float(low_clamped) <= 0.95
        and 0.05 <= float(high_clamped) <= 0.95
        and float(boosted) > 0.70
        and gate_res["should_surface"] is False
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P8-HITL-BAYESIAN-LEARNING",
        pipeline_id=PIPELINE_P8_ID,
        pipeline_name=PIPELINE_P8_NAME,
        title="HITL 20% SSOT Ambiguity Margin & Bounded Bayesian Calibration",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=["SSOT_20_PERCENT_MARGIN_GATE", "BAYESIAN_POSTERIOR_CLAMPING_005_095"],
        metrics={
            "ambiguous_15pct": ambiguous,
            "decisive_35pct": decisive,
            "boosted_score": round(float(boosted), 4),
        },
    )
