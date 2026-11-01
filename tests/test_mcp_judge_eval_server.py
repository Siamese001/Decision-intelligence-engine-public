"""Tests for LLM Judge & Retrieval Evaluation MCP Server (Rank 4)."""

import json
from pathlib import Path

from tools.mcp_judge_eval_server import (
    calibrate_hybrid_weights,
    eval_retrieval_11gates,
    judge_evaluate_candidate,
    seal_retrieval_receipt,
)


def test_judge_evaluate_candidate():
    valid_text = "Forensic content\n## Ambiguity Lock Register\n| ALR-01 | None | N/A | High confidence | RESOLVED |"
    res = judge_evaluate_candidate("forensic_fidelity", valid_text)
    data = json.loads(res)
    assert data["rubric_id"] == "forensic_fidelity"
    assert data["verdict"] in ("PASS", "ESCALATE")
    assert data["confidence_score"] > 0.6


def test_judge_evaluate_candidate_truncation():
    large_context = json.dumps({"data": "x" * 20000})
    res = judge_evaluate_candidate(
        "governance_compliance",
        "Clean candidate text",
        context_envelope_json=large_context,
        token_budget=100,
    )
    data = json.loads(res)
    assert data["context_truncated"] is True
    assert data["token_budget_applied"] == 100


def test_eval_retrieval_11gates():
    res = eval_retrieval_11gates(synthetic_allowed=False)
    data = json.loads(res)
    assert "gates" in data
    assert len(data["gates"]) == 11
    # Canonical schema key
    assert data["gates"]["real_bge_m3"]["passed"] is True
    assert "pair_binding" in data["gates"]
    assert "coverage_complete" in data["gates"]
    assert "core_thresholds" in data["gates"]
    assert "bm25_execution" in data["gates"]
    # Legacy alias support
    assert "legacy_aliases" in data
    assert data["legacy_aliases"]["gate_04_no_synthetic_embeddings"]["passed"] is True


def test_seal_retrieval_receipt(tmp_path: Path):
    eval_results = {
        "status": "QUALIFIED",
        "all_gates_passed": True,
        "gates": {"real_bge_m3": {"passed": True}},
    }
    res = seal_retrieval_receipt(json.dumps(eval_results), output_dir=str(tmp_path))
    data = json.loads(res)
    assert data["status"] == "RECEIPT_SEALED"
    assert "receipt_digest" in data
    assert data["contract_id"] == "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
    receipt_file = Path(data["receipt_path"])
    assert receipt_file.is_file()
    saved = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert saved["contract_id"] == "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
    assert "canonical_digest" in saved


def test_calibrate_hybrid_weights_legacy_3engine():
    res = calibrate_hybrid_weights(dense_weight=1.0, sparse_weight=0.5, colbert_weight=0.5)
    data = json.loads(res)
    assert data["status"] == "CALIBRATED"
    weights = data["normalized_weights"]
    assert weights["dense_weight"] == 0.5
    assert weights["sparse_weight"] == 0.25
    assert weights["colbert_weight"] == 0.25
    assert data["sum"] == 1.0


def test_calibrate_hybrid_weights_quad_engine_default():
    res = calibrate_hybrid_weights()
    data = json.loads(res)
    assert data["status"] == "CALIBRATED"
    weights = data["normalized_weights"]
    assert weights["dense_weight"] == 0.05
    assert weights["sparse_weight"] == 0.15
    assert weights["bm25_weight"] == 0.70
    assert weights["colbert_weight"] == 0.10
    assert data["sum"] == 1.0
    assert data["engine_count"] == 4
