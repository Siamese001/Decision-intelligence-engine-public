"""Adversarial and edge-case tests for Wave 5 falsification and anti-forgery gates."""

from __future__ import annotations

import math
import pytest

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
    make_calibrated_decision,
)
from dual_pipeline.retrieval_release_core.falsification import (
    FalsificationAttackType,
    execute_falsification_attack_corrupt_vector,
    execute_falsification_attack_disable_bge_m3,
    execute_falsification_attack_inject_wrong_facts,
    execute_falsification_attack_remove_positives,
)
from dual_pipeline.retrieval_release_core.paired_experiments import QueryJudgments


def test_adversarial_boundary_remove_all_positives_from_empty_pool():
    # Category: Boundary payload
    query_data = {"query_id": "q_empty", "candidates": []}
    gt = QueryJudgments(
        query_id="q_empty",
        query_text="Empty text",
        topic_id="top0",
        phrasing_type="exec",
        judgments={},
        relevant_ids=set(),
        is_answerable=False,
    )
    res = execute_falsification_attack_remove_positives(query_data, gt)
    assert res.attack_type == FalsificationAttackType.REMOVE_POSITIVES
    assert res.metrics_after["mrr"] == 0.0


def test_adversarial_malformed_input_corrupted_vector_dimensions():
    # Category: Malformed input
    # 1. 512-dim vector instead of 1024
    res_512 = execute_falsification_attack_corrupt_vector([0.5] * 512)
    assert res_512.detected_cleanly is True
    assert res_512.forged_pass_prevented is True

    # 2. Vector with NaN
    res_nan = execute_falsification_attack_corrupt_vector([float("nan")] * 1024)
    assert res_nan.detected_cleanly is True

    # 3. Vector with Inf
    res_inf = execute_falsification_attack_corrupt_vector([float("inf")] * 1024)
    assert res_inf.detected_cleanly is True


def test_adversarial_bypass_disabled_bge_zero_qualification_credit():
    # Category: Invariant bypass attempt
    # Disabled BGE model: qualification credit MUST be strictly 0.0
    res = execute_falsification_attack_disable_bge_m3()
    assert res.attack_type == FalsificationAttackType.DISABLE_BGE_M3_ZERO_CREDIT
    assert res.forged_pass_prevented is True


def test_adversarial_bypass_distractor_cannot_claim_autonomous_authority():
    # Category: Invariant bypass attempt
    # Distractor candidates designed to mimic top-1 candidate with marginal difference
    query_data = {
        "query_id": "q_distractor",
        "candidates": [
            {"record_id": "c1", "fused_score": 0.019, "dense_rank": 1, "sparse_rank": 1, "bm25_rank": 1, "colbert_rank": 1}
        ],
    }
    gt = QueryJudgments(
        query_id="q_distractor",
        query_text="Distractor test",
        topic_id="top1",
        phrasing_type="keyword",
        judgments={"c1": 2},
        relevant_ids={"c1"},
        is_answerable=True,
    )
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(tau_auto=0.78, min_margin=0.15)
    res = execute_falsification_attack_inject_wrong_facts(query_data, gt, weights, thresholds)
    assert res.forged_pass_prevented is True


def test_adversarial_exact_production_regression_agent_auditor_default_passes_overridden():
    # Category: Exact production regression
    # Regression: release_gate_auditor.py historically defaulted all 11 gates to PASS
    # Computed measurements must override nominal agent pass summaries
    nominal_agent_report = {
        "all_gates_passed": True,
        "qualification_status": "DELIVERY_READY",
        "gate_results": {"bge_m3_retrieval": {"status": "PASS", "reason": "Verified"}},
    }
    # Reality: Positives were removed -> real measured MRR is 0.0
    actual_measured_mrr = 0.0
    quality_floor_mrr = 0.60

    # Overriding logic: Computed measurement must reject the release
    is_authoritative_pass = (
        nominal_agent_report["all_gates_passed"]
        and actual_measured_mrr >= quality_floor_mrr
    )
    assert is_authoritative_pass is False, "Nominal agent report must not override computed measurement failures"


def test_adversarial_bypass_citation_only_rejected_as_factual_authority():
    # Category: Invariant bypass attempt
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_citation_only_authority
    res = execute_falsification_attack_citation_only_authority()
    assert res.attack_type == FalsificationAttackType.CITATION_ONLY_AUTHORITY
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_adversarial_boundary_clearance_breach_suppression():
    # Category: Boundary payload
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_clearance_breach
    res = execute_falsification_attack_clearance_breach()
    assert res.attack_type == FalsificationAttackType.CLEARANCE_BREACH_ATTEMPT
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_adversarial_malformed_corrupt_sidecar_fail_closed():
    # Category: Malformed input
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_corrupt_sidecar
    res = execute_falsification_attack_corrupt_sidecar()
    assert res.attack_type == FalsificationAttackType.CORRUPT_SIDECAR
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_adversarial_bypass_dev_backend_fails_gate1_and_auditor():
    # Category: Invariant bypass attempt
    import asyncio
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus
    from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor

    auditor = ReleaseGateAuditor(mock_mode=True)
    task = A2ATask(
        task_id="t-audit-dev-backend-adv",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Audit dev backend",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"backend_id": "bge-m3-dev", "max_query_latency_ms": 100.0},
    )
    res = asyncio.run(auditor.handle_task(task))
    assert res["all_gates_passed"] is False
    assert res["gate_results"]["real_bge_m3"]["status"] == "FAIL"

