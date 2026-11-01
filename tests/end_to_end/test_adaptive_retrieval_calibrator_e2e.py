"""End-to-end verification of AdaptiveRetrievalCalibratorAgent, ClosedLoopLearningEngine, and TokenOptimizationAgent."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import (
    ALL_ELEVEN_BGE_GATES,
    AdaptiveRetrievalCalibratorAgent,
    clamp_route_multiplier,
)
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
from dual_pipeline.token_optimization.schemas import ReasoningEffortTier


def _init_sandbox_graph(db_path: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, payload TEXT);"
            "CREATE TABLE IF NOT EXISTS edges (id TEXT PRIMARY KEY, payload TEXT);"
            "CREATE TABLE IF NOT EXISTS assertions (id TEXT PRIMARY KEY, payload TEXT);"
            "CREATE TABLE IF NOT EXISTS evidence_items (id TEXT PRIMARY KEY, payload TEXT);"
            "CREATE TABLE IF NOT EXISTS schema_version (id TEXT PRIMARY KEY, payload TEXT);"
            "INSERT INTO schema_version VALUES ('1', '1.0.0');"
        )
        conn.commit()
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


def test_adaptive_retrieval_calibrator_emits_schema_valid_learning_proposal_e2e(tmp_path: Path) -> None:
    """Verifies 3-step failure cohort analysis, 11-gate holdout check, CLLE simulation, and UWG commit."""
    proposals_dir = tmp_path / "proposals"
    calibrator = AdaptiveRetrievalCalibratorAgent(max_steps=3, max_tool_calls=6, proposals_dir=proposals_dir)

    failure_cohort = [
        {
            "event_id": "ev_fail_cohort_0001",
            "missed_dimension": "relational",
            "suggested_multiplier": 0.58,
            "query": "How do PDLC capabilities connect to workstream risks?",
        },
        {
            "event_id": "ev_fail_cohort_0002",
            "missed_dimension": "temporal",
            "suggested_multiplier": 0.52,
            "query": "When was the latest status snapshot recorded?",
        },
    ]
    current_multipliers = {
        "relational": 0.45,
        "temporal": 0.45,
        "strategic": 0.45,
        "analytical": 0.45,
        "concept": 0.40,
    }
    baseline_metrics = {
        "ndcg_at_10": 0.81,
        "recall_at_50": 0.88,
        "mrr_at_10": 0.79,
        "contradiction_pair_recall": 0.90,
        "provenance_preservation_rate": 1.00,
        "temporal_status_accuracy": 0.92,
        "entity_anchor_hit_rate": 0.89,
        "multi_hop_bridge_coverage": 0.84,
        "p95_latency_ms": 140.0,
        "stale_leakage_rate": 0.00,
        "candidate_fact_leakage": 0.00,
    }
    candidate_metrics = {
        "ndcg_at_10": 0.86,
        "recall_at_50": 0.92,
        "mrr_at_10": 0.82,
        "contradiction_pair_recall": 0.92,
        "provenance_preservation_rate": 1.00,
        "temporal_status_accuracy": 0.94,
        "entity_anchor_hit_rate": 0.91,
        "multi_hop_bridge_coverage": 0.89,
        "p95_latency_ms": 135.0,
        "stale_leakage_rate": 0.00,
        "candidate_fact_leakage": 0.00,
    }
    assert len(ALL_ELEVEN_BGE_GATES) == 11

    outcome = calibrator.calibrate(
        failure_cohort=failure_cohort,
        current_multipliers=current_multipliers,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        proposed_dense_weight=0.62,
        proposed_sparse_weight=0.38,
    )

    assert outcome.accepted is True
    assert outcome.rejection_reasons == []
    assert outcome.steps_executed == 3
    assert outcome.terminal_outcome == "COMPLETED"
    assert outcome.clamped_multipliers["relational"] == 0.58
    assert outcome.clamped_multipliers["temporal"] == 0.52
    assert all(0.20 <= v <= 0.70 for v in outcome.clamped_multipliers.values())
    assert round(outcome.hybrid_weights["dense_weight"] + outcome.hybrid_weights["sparse_weight"], 4) == 1.0

    proposal = outcome.proposal_artifact
    assert proposal is not None
    assert proposal["contract_id"] == "urn:nee:dual-pipeline:learning-change-proposal:1.0.0"
    assert proposal["approval_required"] is True
    assert proposal["auto_apply"] is False
    assert proposal["status"] == "proposed"
    validate_artifact(proposal)
    assert (proposals_dir / f"{proposal['proposal_id']}.json").is_file()

    # Verify ClosedLoopLearningEngine provenance, 11-gate simulation, and UWG commit
    _init_sandbox_graph(tmp_path / "seed_graph.sqlite")
    config_path = tmp_path / "parameters.json"
    write_json(
        config_path,
        {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {"retrieval": {"route_multiplier": {"relational": 0.45, "temporal": 0.45}}},
        },
    )
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=proposals_dir)
    prov_ok, prov_msg = engine.verify_provenance(proposal)
    assert prov_ok is True, prov_msg

    sim_res = engine.simulate_proposal(proposal)
    assert sim_res.success is True
    assert sim_res.status == "SIMULATED_PASS"
    assert sim_res.details["bge_m3_gates_verified"] is True
    assert (proposals_dir / f"{proposal['proposal_id']}_simulation_receipt.json").is_file()

    approval = create_signed_calibration_approval_receipt(
        proposal=proposal,
        approved_by="human_operator_lead",
        rationale="Approved calibrated route multiplier after 11-gate verification",
    )
    apply_receipt = engine.apply_approved_proposal(
        proposal,
        approval,
        parameter_path=config_path,
        skip_decision_clearance=True,
    )
    assert apply_receipt["status"] == "APPLIED"
    reg = ParameterRegistry(config_path=config_path)
    assert reg.get("retrieval.route_multiplier.relational") == 0.58
    saved_apply = read_json(proposals_dir / f"{proposal['proposal_id']}_applied_receipt.json")
    assert saved_apply["proposal_digest"] == proposal["canonical_digest"]["value"]


def test_calibrator_recall_only_improvement_passes_clle_simulation_e2e(tmp_path: Path) -> None:
    """Verifies that a recall_at_50 improvement with 0.0 ndcg_at_10 delta selects recall_at_50 and passes simulation."""
    calibrator = AdaptiveRetrievalCalibratorAgent(proposals_dir=tmp_path / "proposals")
    baseline = {"ndcg_at_10": 0.85, "recall_at_50": 0.88, "p95_latency_ms": 120.0}
    candidate = {"ndcg_at_10": 0.85, "recall_at_50": 0.93, "p95_latency_ms": 118.0}
    outcome = calibrator.calibrate(
        failure_cohort=[{"event_id": "ev_rec_01", "missed_dimension": "relational", "suggested_multiplier": 0.55}],
        current_multipliers={"relational": 0.45},
        baseline_metrics=baseline,
        candidate_metrics=candidate,
    )
    assert outcome.accepted is True
    assert outcome.proposal_artifact is not None
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")
    sim_res = engine.simulate_proposal(outcome.proposal_artifact)
    assert sim_res.success is True
    assert sim_res.details["target_metric"] == "recall_at_50"
    assert sim_res.details["target_metric_gain"] == 0.05


def test_token_optimization_agent_calibrates_from_runtime_loop_telemetry_e2e() -> None:
    """Verifies dynamic reasoning tier escalation when an AgentRuntimeLoop stagnates or exhausts budget."""
    agent = TokenOptimizationAgent()

    low_effort = agent.calibrate_from_runtime_loop(
        "router_classification",
        {"terminal_outcome": "COMPLETED", "steps_executed": 1},
    )
    assert low_effort == ReasoningEffortTier.LOW

    stagnated_effort = agent.calibrate_from_runtime_loop(
        "router_classification",
        {"terminal_outcome": "STAGNATED", "steps_executed": 2},
    )
    assert stagnated_effort == ReasoningEffortTier.HIGH

    exhausted_effort = agent.calibrate_from_runtime_loop(
        "router_classification",
        {"terminal_outcome": "BUDGET_EXHAUSTED", "steps_executed": 3},
    )
    assert exhausted_effort == ReasoningEffortTier.XHIGH
    assert clamp_route_multiplier(0.55) == 0.55
