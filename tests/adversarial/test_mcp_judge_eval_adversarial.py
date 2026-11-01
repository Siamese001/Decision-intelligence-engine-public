"""Adversarial and boundary regression tests for LLM Judge & Retrieval Eval MCP Server (Rank 4).

Covers:
1. Boundary & malformed payloads (zero/negative weights, JSON syntax errors, extreme token budgets)
2. Invariant bypass attempts (synthetic embedding gating, corrupted receipt payload injection)
3. Production regression pins (prohibited TODO/FIXME markers, strict non-fabrication of unmeasured gates)
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.mcp_judge_eval_server import (
    calibrate_hybrid_weights,
    eval_retrieval_11gates,
    judge_evaluate_candidate,
    seal_retrieval_receipt,
)


def test_adversarial_weights_zero_and_negative():
    """Verify that zero or negative hybrid weights fail closed with error."""
    res_zero = calibrate_hybrid_weights(
        dense_weight=0.0, sparse_weight=0.0, bm25_weight=0.0, colbert_weight=0.0
    )
    data_zero = json.loads(res_zero)
    assert "error" in data_zero
    assert "positive" in data_zero["error"]

    res_neg = calibrate_hybrid_weights(
        dense_weight=-0.5, sparse_weight=-0.5, bm25_weight=-0.5, colbert_weight=-0.5
    )
    data_neg = json.loads(res_neg)
    assert "error" in data_neg


def test_adversarial_judge_malformed_graph_projection():
    """Verify that broken JSON in graph projection rubric yields 0 score and explicit error."""
    broken_payloads = [
        "{unquoted_key: 123}",
        "[]",
        '{"foo": "bar"}',  # Missing statements
        '{"statements": null}',
    ]
    for payload in broken_payloads:
        res = judge_evaluate_candidate("graph_projection", payload)
        data = json.loads(res)
        assert data["verdict"] in ("FAIL", "ESCALATE")
        assert len(data["errors"]) > 0


def test_adversarial_judge_prohibited_markers():
    """Verify that production purity markers (TODO, PLACEHOLDER, FIXME) trigger governance failure."""
    for marker in ["TODO", "PLACEHOLDER", "FIXME"]:
        res = judge_evaluate_candidate(
            "governance_compliance", f"This is an implementation {marker}: do later."
        )
        data = json.loads(res)
        assert any(marker in err for err in data["errors"])
        assert data["verdict"] != "PASS"


def test_adversarial_token_budget_boundaries():
    """Test extreme token budgets: 0 tokens, negative tokens, and large tokens."""
    # Zero token budget
    res_zero = judge_evaluate_candidate(
        "governance_compliance",
        "Valid text",
        context_envelope_json='{"key": "value"}',
        token_budget=0,
    )
    data_zero = json.loads(res_zero)
    assert data_zero["token_budget_applied"] == 0

    # Negative token budget should not raise unhandled exception
    res_neg = judge_evaluate_candidate(
        "governance_compliance",
        "Valid text",
        context_envelope_json='{"key": "value"}',
        token_budget=-10,
    )
    data_neg = json.loads(res_neg)
    assert "verdict" in data_neg or "error" in data_neg


def test_adversarial_seal_corrupted_json(tmp_path: Path):
    """Test that malformed JSON input to seal_retrieval_receipt fails gracefully."""
    res = seal_retrieval_receipt("{{{not valid json", output_dir=str(tmp_path))
    data = json.loads(res)
    assert "error" in data
    assert "Failed to seal" in data["error"]


def test_adversarial_synthetic_embedding_invariant():
    """Verify that synthetic embeddings fail closed when synthetic_allowed is False."""
    res = eval_retrieval_11gates(synthetic_allowed=False)
    data = json.loads(res)
    assert "gates" in data
    assert "real_bge_m3" in data["gates"]
    assert (
        data["gates"]["real_bge_m3"]["threshold"]
        == "synthetic_allowed == False and valid BGE-M3 vectors"
    )


def test_adversarial_no_fabricated_scores_on_unmeasured_gates():
    """Regression pin: unmeasured retrieval quality gates must NEVER return hardcoded 0.85/0.88."""
    # When evaluated without an executed evaluation receipt
    res = eval_retrieval_11gates(runtime_root="/nonexistent/runtime/path", synthetic_allowed=False)
    data = json.loads(res)
    gates = data["gates"]

    # Quality gates must report measured=False, not fabricated static numbers
    for g_name in (
        "core_thresholds",
        "delta_thresholds",
        "critical_cases",
        "rank_stability",
        "repeat_stability",
        "forbidden_leakage",
    ):
        assert gates[g_name]["measured"] is False
        assert gates[g_name]["passed"] is False
        assert gates[g_name]["metric"] is None
