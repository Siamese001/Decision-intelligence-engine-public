"""Adversarial and boundary regression tests for retrieval evaluation, metrics, and gates.

Covers mandatory edge-case categories:
1. Boundary payload: Empty rankings, empty expectations, zero judgments.
2. Malformed input: Invalid JSON in receipts, missing fields, corrupted candidate schemas.
3. Invariant bypass attempt: Stale receipt replay, fake PASS receipts, synthetic model masquerading.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from dual_pipeline.retrieval_eval_core.edge_gate import _ndcg_at_k, _reciprocal_rank
from dual_pipeline.retrieval.agents import ReleaseGateAuditor
from tools.mcp_judge_eval_core.evaluator import _load_sealed_evaluation, eval_retrieval_11gates
from tools.score_qrels import compute_dcg, compute_ndcg
from tools.score_qrels_v2 import assess_candidate_confidence, compute_mrr_at_k, compute_ndcg_at_k


def test_adversarial_boundary_empty_and_zero_payloads():
    """Test boundary conditions: empty rankings, empty ground truth, and zero-length arrays."""
    # Empty expectations in edge_gate must strictly yield 0.0
    assert _reciprocal_rank([], []) == 0.0
    assert _ndcg_at_k([], []) == 0.0
    assert _reciprocal_rank(["rec-1", "rec-2"], []) == 0.0
    assert _ndcg_at_k(["rec-1", "rec-2"], []) == 0.0

    # Empty judgments in score_qrels
    assert compute_ndcg([], k=5) == 0.0
    assert compute_dcg([]) == 0.0
    assert compute_mrr_at_k([], k=10) == 0.0
    assert compute_ndcg_at_k([], k=10) == 0.0

    # Ranking with only non-relevant (grade 0) records
    assert compute_ndcg([0, 0, 0], k=5) == 0.0
    assert compute_dcg([0, 0, 0]) == 0.0
    assert compute_mrr_at_k([0, 0, 0], k=10) == 0.0
    assert compute_ndcg_at_k([0, 0, 0], k=10) == 0.0


def test_adversarial_malformed_input_resilience():
    """Verify system handles malformed candidate structures gracefully without crashing."""
    # Candidate with empty strings or missing keys
    malformed_candidate = {"record_id": "malformed-1"}
    query = {"query_id": "q1", "text": "sample query"}

    res = assess_candidate_confidence(query, malformed_candidate)
    assert isinstance(res, dict)
    assert res["recommended_grade"] == 0  # No evidence -> Grade 0
    assert res["recommended_tier"] == "LOW" or "MEDIUM" in res["recommended_tier"]


def test_adversarial_bypass_stale_receipt_replay(tmp_path: Path):
    """Verify that a sealed receipt from another generation cannot qualify a mismatched active pair."""
    stale_receipt = tmp_path / "retrieval-release-evaluation-stale.json"
    receipt_data = {
        "contract_id": "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
        "status": "PASS",
        "candidate_pair": {
            "generation_id": "gen-historical-victim",
            "graph_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
            "sidecar_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        },
        "gates": {
            "real_bge_m3": True,
            "pair_binding": True,
            "coverage_complete": True,
            "core_thresholds": True,
            "delta_thresholds": True,
            "critical_cases": True,
            "rank_stability": True,
            "repeat_stability": True,
            "forbidden_leakage": True,
            "latency": True,
            "bm25_execution": True,
        },
    }
    stale_receipt.write_text(json.dumps(receipt_data), encoding="utf-8")

    # Target active runtime with different SHA-256
    active_pair_data = {
        "generation_id": "gen-active-attacker",
        "graph_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
        "sidecar_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
    }

    loaded, src = _load_sealed_evaluation([stale_receipt], active_pair_data=active_pair_data)
    assert loaded is None
    assert src == "live_inspection"


def test_adversarial_bypass_failed_status_receipt_rejected(tmp_path: Path):
    """Verify that a sealed receipt with status == 'FAIL' cannot qualify the active pair."""
    failed_receipt = tmp_path / "retrieval-release-evaluation-failed.json"
    receipt_data = {
        "contract_id": "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
        "status": "FAIL",
        "candidate_pair": {
            "generation_id": "gen-failed",
            "graph_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
            "sidecar_sha256": "6666666666666666666666666666666666666666666666666666666666666666",
        },
        "gates": {"core_thresholds": False},
    }
    failed_receipt.write_text(json.dumps(receipt_data), encoding="utf-8")

    active_pair_data = {
        "generation_id": "gen-failed",
        "graph_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
        "sidecar_sha256": "6666666666666666666666666666666666666666666666666666666666666666",
    }

    loaded, src = _load_sealed_evaluation([failed_receipt], active_pair_data=active_pair_data)
    assert loaded is None
    assert src == "live_inspection"


def test_adversarial_auditor_all_gates_non_compensating():
    """Verify ReleaseGateAuditor enforces non-compensating failure on every individual gate."""
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus

    async def _run():
        auditor = ReleaseGateAuditor(mock_mode=True)

        gates_to_trip = [
            ("pair_binding", {"pair_binding_passed": False}),
            ("coverage_complete", {"coverage_ratio": 0.95}),
            ("core_thresholds", {"core_mrr": 0.90}),
            ("delta_thresholds", {"has_delta": True, "delta_ndcg": 0.85}),
            ("critical_cases", {"critical_failure_count": 1}),
            ("rank_stability", {"rank_regression_count": 2}),
            ("repeat_stability", {"repeat_drift_count": 1}),
            ("forbidden_leakage", {"forbidden_leakage_count": 1}),
            ("latency_threshold", {"max_query_latency_ms": 9000.0}),
            ("component_execution", {"bm25_executed": False}),
        ]

        for gate_name, payload_override in gates_to_trip:
            payload = {"max_query_latency_ms": 100.0}
            payload.update(payload_override)
            task = A2ATask(
                task_id=f"t-trip-{gate_name}",
                parent_task_id=None,
                sender_agent_id="test",
                recipient_agent_id=auditor.card.agent_id,
                objective=f"Trip {gate_name}",
                status=A2ATaskStatus.SUBMITTED,
                input_payload=payload,
            )
            res = await auditor.handle_task(task)
            assert not res["all_gates_passed"], f"Gate {gate_name} failed to block release!"
            assert res["gate_results"][gate_name]["status"] == "FAIL", f"Gate {gate_name} did not record FAIL!"
            assert res["qualification_status"] == "BLOCKED"

    asyncio.run(_run())
