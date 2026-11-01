"""Unit tests verifying forensic audit fixes across retrieval evaluation, metrics, and gates.

Verifies:
1. Circular bias elimination in score_qrels_v2.assess_candidate_confidence.
2. Zero return on empty expectations in edge_gate._reciprocal_rank and _ndcg_at_k.
3. Standard TREC MRR calculation in qrels_weight_calibration.compute_mrr_at_k.
4. Exact logarithmic decay nDCG in SparkRetrievalReleaseEvaluator.
5. Authentic gate rejection in ReleaseGateAuditor.
6. Stale receipt replay rejection in evaluator._load_sealed_evaluation.
"""
from __future__ import annotations

import asyncio
import inspect
import math
from pathlib import Path
import pytest

from dual_pipeline.retrieval_eval_core.edge_gate import _ndcg_at_k, _reciprocal_rank
from dual_pipeline.retrieval.agents import ReleaseGateAuditor
from dual_pipeline.spark.retrieval_eval_spark import SparkRetrievalReleaseEvaluator
from evals.qrels_weight_calibration import compute_mrr_at_k
from tools.mcp_judge_eval_core.evaluator import _load_sealed_evaluation
from tools.score_qrels_v2 import assess_candidate_confidence


def test_circular_bias_eliminated_in_score_qrels_v2():
    """Verify that retriever output parameters (dense_score, fused_rank) are absent from ground truth grading."""
    sig = inspect.signature(assess_candidate_confidence)
    # The signature must not accept dense_score or fused_rank
    assert "dense_score" not in sig.parameters
    assert "fused_rank" not in sig.parameters

    query = {
        "query_id": "q1",
        "text": "How many replicas are required for the storage cluster?",
        "target_entities": ["storage cluster"],
        "ground_truth_summary": "3 replicas across availability zones",
    }
    candidate = {
        "record_id": "rec-1",
        "retrieval_text": "The distributed storage cluster requires 3 replicas across availability zones.",
        "title": "Cluster Replicas",
        "record_type": "source_chunk",
    }

    res = assess_candidate_confidence(query, candidate)
    assert isinstance(res, dict)
    assert res["recommended_grade"] in (0, 1, 2)
    assert "recommended_confidence" in res
    assert "recommended_tier" in res


def test_empty_expectation_returns_zero_in_edge_gate():
    """Verify that empty expected_record_ids returns 0.0, avoiding false PASS on unanswerable queries."""
    # When expected is empty, any retrieved record must yield 0.0, not 1.0
    assert _reciprocal_rank(["rec-01"], []) == 0.0
    assert _ndcg_at_k(["rec-01"], []) == 0.0

    # Normal positive case still calculates correctly
    assert _reciprocal_rank(["rec-01"], ["rec-01"]) == 1.0
    assert _ndcg_at_k(["rec-01"], ["rec-01"]) == 1.0


def test_standard_trec_mrr_in_weight_calibration():
    """Verify TREC Reciprocal Rank in qrels_weight_calibration."""
    # At threshold 1: first relevant hit at index 0 (rank 1) -> MRR = 1.0
    judgments_rank1 = [1, 0, 2]
    mrr_t1 = compute_mrr_at_k(judgments_rank1, k=10, relevance_threshold=1)
    assert mrr_t1 == 1.0

    # At threshold 2: grade 1 at rank 1 is not relevant; grade 2 at index 2 (rank 3) -> MRR = 1/3
    mrr_t2 = compute_mrr_at_k(judgments_rank1, k=10, relevance_threshold=2)
    assert pytest.approx(mrr_t2, abs=1e-6) == 1.0 / 3.0

    # Empty judgments yields 0.0
    assert compute_mrr_at_k([], k=10) == 0.0

    # No items meet threshold yields 0.0
    assert compute_mrr_at_k([0, 0, 0], k=10, relevance_threshold=1) == 0.0


def test_true_logarithmic_ndcg_in_spark_evaluator():
    """Verify exact logarithmic decay for nDCG calculation in Spark evaluator."""
    evaluator = SparkRetrievalReleaseEvaluator(mock_mode=True)

    # Rank 1: 1.0
    res_rank1 = evaluator.evaluate_queries_distributed([{"query_id": "q1", "golden_rank": 1}])
    assert res_rank1.metrics["ndcg_at_10"] == 1.0

    # Rank 2: round(1 / log2(3), 4) == 0.6309
    res_rank2 = evaluator.evaluate_queries_distributed([{"query_id": "q2", "golden_rank": 2}])
    expected_rank2 = round(1.0 / math.log2(3), 4)
    assert res_rank2.metrics["ndcg_at_10"] == expected_rank2

    # Rank 3: round(1 / log2(4), 4) == 0.5
    res_rank3 = evaluator.evaluate_queries_distributed([{"query_id": "q3", "golden_rank": 3}])
    assert res_rank3.metrics["ndcg_at_10"] == 0.5

    # Rank > 10: 0.0
    res_rank11 = evaluator.evaluate_queries_distributed([{"query_id": "q11", "golden_rank": 11}])
    assert res_rank11.metrics["ndcg_at_10"] == 0.0


def test_release_gate_auditor_genuine_rejection():
    """Verify that ReleaseGateAuditor genuinely evaluates all 11 gates and rejects failing conditions."""
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus

    async def _run():
        auditor = ReleaseGateAuditor(mock_mode=True)

        # Case 1: Insufficient core thresholds (MRR < 1.0)
        task_low_mrr = A2ATask(
            task_id="t-audit-mrr",
            parent_task_id=None,
            sender_agent_id="test",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit low MRR",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "core_metrics": {"recall_at_10": 1.0, "mrr": 0.80, "ndcg": 1.0},
            },
        )
        res_low_mrr = await auditor.handle_task(task_low_mrr)
        assert not res_low_mrr["all_gates_passed"]
        assert res_low_mrr["gate_results"]["core_thresholds"]["status"] == "FAIL"
        assert res_low_mrr["qualification_status"] == "BLOCKED"

        # Case 2: Synthetic embedding backend prohibited
        task_synthetic = A2ATask(
            task_id="t-audit-synthetic",
            parent_task_id=None,
            sender_agent_id="test",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit synthetic backend",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"embedding_backend": "synthetic-mock"},
        )
        res_synthetic = await auditor.handle_task(task_synthetic)
        assert not res_synthetic["all_gates_passed"]
        assert res_synthetic["gate_results"]["real_bge_m3"]["status"] == "FAIL"

        # Case 3: Forbidden leakage detected
        task_leak = A2ATask(
            task_id="t-audit-leak",
            parent_task_id=None,
            sender_agent_id="test",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit leakage",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"forbidden_leakage_count": 2},
        )
        res_leak = await auditor.handle_task(task_leak)
        assert not res_leak["all_gates_passed"]
        assert res_leak["gate_results"]["forbidden_leakage"]["status"] == "FAIL"

    asyncio.run(_run())


def test_stale_receipt_replay_prevention(tmp_path: Path):
    """Verify that _load_sealed_evaluation rejects receipts with mismatched graph/sidecar SHA256."""
    import json

    receipt_file = tmp_path / "retrieval-release-evaluation-test.json"
    receipt_data = {
        "contract_id": "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
        "status": "PASS",
        "candidate_pair": {
            "generation_id": "gen-old",
            "graph_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "sidecar_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        },
        "gates": {"core_thresholds": True},
    }
    receipt_file.write_text(json.dumps(receipt_data), encoding="utf-8")

    # Mismatched active pair must be rejected
    mismatched_active_pair = {
        "generation_id": "gen-new",
        "graph_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "sidecar_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    }
    loaded, src = _load_sealed_evaluation([receipt_file], active_pair_data=mismatched_active_pair)
    assert loaded is None
    assert src == "live_inspection"

    # Matching active pair must load successfully
    matching_active_pair = {
        "generation_id": "gen-old",
        "graph_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sidecar_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    loaded_match, src_match = _load_sealed_evaluation(
        [receipt_file], active_pair_data=matching_active_pair
    )
    assert loaded_match is not None
    assert src_match == str(receipt_file)
