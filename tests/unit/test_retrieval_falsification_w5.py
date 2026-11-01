"""Unit tests for Wave 5 falsification attacks and holdout evaluation."""

from __future__ import annotations

import pytest

from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
)
from dual_pipeline.retrieval_release_core.falsification import (
    FalsificationAttackType,
    execute_falsification_attack_corrupt_vector,
    execute_falsification_attack_disable_bge_m3,
    execute_falsification_attack_inject_wrong_facts,
    execute_falsification_attack_remove_positives,
    execute_falsification_attack_reverse_rankings,
)
from dual_pipeline.retrieval_release_core.paired_experiments import QueryJudgments


def test_falsification_remove_positives_collapses_mrr():
    query_data = {
        "query_id": "q1",
        "candidates": [
            {"record_id": "c1", "dense_score": 0.9, "dense_rank": 1, "sparse_rank": 1, "bm25_rank": 1, "colbert_rank": 1},
            {"record_id": "c2", "dense_score": 0.2, "dense_rank": 2, "sparse_rank": 2, "bm25_rank": 2, "colbert_rank": 2},
        ],
    }
    gt = QueryJudgments(
        query_id="q1",
        query_text="Sample text",
        topic_id="top1",
        phrasing_type="exec",
        judgments={"c1": 2, "c2": 0},
        relevant_ids={"c1"},
        is_answerable=True,
    )
    res = execute_falsification_attack_remove_positives(query_data, gt)
    assert res.attack_type == FalsificationAttackType.REMOVE_POSITIVES
    assert res.detected_cleanly is True
    assert res.metrics_after["mrr"] == 0.0
    assert res.metrics_after["ndcg"] == 0.0


def test_falsification_inject_distractor_collapses_margin():
    query_data = {
        "query_id": "q1",
        "candidates": [
            {"record_id": "c1", "fused_score": 0.015, "dense_rank": 1, "sparse_rank": 1, "bm25_rank": 1, "colbert_rank": 1}
        ],
    }
    gt = QueryJudgments(
        query_id="q1",
        query_text="Sample text",
        topic_id="top1",
        phrasing_type="exec",
        judgments={"c1": 2},
        relevant_ids={"c1"},
        is_answerable=True,
    )
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(min_margin=0.15)
    res = execute_falsification_attack_inject_wrong_facts(query_data, gt, weights, thresholds)
    assert res.attack_type == FalsificationAttackType.INJECT_PLAUSIBLE_WRONG_FACTS
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_reverse_rankings_degrades_metrics():
    query_data = {
        "query_id": "q1",
        "candidates": [
            {"record_id": "c_pos", "dense_score": 0.9, "dense_rank": 1, "sparse_rank": 1, "bm25_rank": 1, "colbert_rank": 1},
            {"record_id": "c_neg1", "dense_score": 0.5, "dense_rank": 2, "sparse_rank": 2, "bm25_rank": 2, "colbert_rank": 2},
            {"record_id": "c_neg2", "dense_score": 0.1, "dense_rank": 3, "sparse_rank": 3, "bm25_rank": 3, "colbert_rank": 3},
        ],
    }
    gt = QueryJudgments(
        query_id="q1",
        query_text="Sample text",
        topic_id="top1",
        phrasing_type="exec",
        judgments={"c_pos": 2, "c_neg1": 0, "c_neg2": 0},
        relevant_ids={"c_pos"},
        is_answerable=True,
    )
    res = execute_falsification_attack_reverse_rankings(query_data, gt)
    assert res.attack_type == FalsificationAttackType.REVERSE_RANKINGS
    assert res.detected_cleanly is True
    assert res.metrics_after["ndcg"] < res.metrics_before["ndcg"]
    assert res.metrics_after["mrr"] < res.metrics_before["mrr"]


def test_falsification_corrupt_vector_dimension_rejected():
    res = execute_falsification_attack_corrupt_vector([0.1] * 1024)
    assert res.attack_type == FalsificationAttackType.CORRUPT_VECTOR_DIMENSIONS
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_disabled_bge_assigns_zero_credit():
    res = execute_falsification_attack_disable_bge_m3()
    assert res.attack_type == FalsificationAttackType.DISABLE_BGE_M3_ZERO_CREDIT
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_clearance_breach_suppressed():
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_clearance_breach
    res = execute_falsification_attack_clearance_breach()
    assert res.attack_type == FalsificationAttackType.CLEARANCE_BREACH_ATTEMPT
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_citation_only_authority_fails_validate_answer():
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_citation_only_authority
    res = execute_falsification_attack_citation_only_authority()
    assert res.attack_type == FalsificationAttackType.CITATION_ONLY_AUTHORITY
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_superseded_and_contradicted_assertions_rejected():
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_superseded_contradicted
    res = execute_falsification_attack_superseded_contradicted()
    assert res.attack_type == FalsificationAttackType.SUPERSEDED_CONTRADICTED
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_falsification_corrupt_sidecar_fails_closed():
    from dual_pipeline.retrieval_release_core.falsification import execute_falsification_attack_corrupt_sidecar
    res = execute_falsification_attack_corrupt_sidecar()
    assert res.attack_type == FalsificationAttackType.CORRUPT_SIDECAR
    assert res.detected_cleanly is True
    assert res.forged_pass_prevented is True


def test_release_gate_auditor_rejects_bge_m3_dev():
    import asyncio
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus
    from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor

    auditor = ReleaseGateAuditor(mock_mode=True)
    task = A2ATask(
        task_id="t-audit-dev-backend",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Audit dev backend",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "embedding_backend": "bge-m3-dev",
            "backend_id": "bge-m3-dev",
            "max_query_latency_ms": 100.0,
        },
    )
    res = asyncio.run(auditor.handle_task(task))
    assert res["all_gates_passed"] is False
    assert res["gate_results"]["real_bge_m3"]["status"] == "FAIL"
    assert "Dev backend" in res["gate_results"]["real_bge_m3"]["reason"]


def test_release_gate_auditor_missing_keys_fail_closed_in_strict_mode():
    import asyncio
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus
    from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor

    auditor = ReleaseGateAuditor(mock_mode=False, strict_payload=True)
    task = A2ATask(
        task_id="t-incomplete-payload",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Audit incomplete payload",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"backend_id": "bge-m3"},
    )
    res = asyncio.run(auditor.handle_task(task))
    assert res["all_gates_passed"] is False
    assert res["qualification_status"] == "BLOCKED"


def test_evaluate_holdout_partition_rejects_dev_and_calibration_queries():
    from dual_pipeline.retrieval_release_core.falsification import evaluate_holdout_partition

    invalid_queries = [
        {"query_id": "q_dev_01", "partition": "dev", "candidates": []}
    ]
    gt_map = {
        "q_dev_01": QueryJudgments(
            query_id="q_dev_01",
            query_text="dev",
            topic_id="top1",
            phrasing_type="exec",
            judgments={},
            relevant_ids=set(),
            is_answerable=True,
        )
    }
    with pytest.raises(ValueError, match="rejected query 'q_dev_01' with non-holdout partition 'dev'"):
        evaluate_holdout_partition(
            invalid_queries, gt_map, CalibrationModelWeights(), OperatingThresholds()
        )
