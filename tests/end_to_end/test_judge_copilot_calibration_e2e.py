"""End-to-End tests for Wave 3: Copilot Quality Gate, Multi-Judge Calibration, and MCP Rubric Integrity."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from evals.copilot_quality_gate import (
    run_copilot_quality_gate,
    _evaluate_case_frames,
    _evaluate_case_composer,
    HOLDOUT_ASSERTIONS,
)
from evals.copilot_scorecard import compute_copilot_scorecard
from dual_pipeline.live_meeting_recorder_core.copilot_composer import LocalCopilotComposer
from dual_pipeline.llm_judge_core.calibration_protocol import compute_expected_calibration_error
from tools.mcp_judge_eval_core.rubrics import judge_evaluate_candidate
from tools.mcp_judge_eval_core.receipt import seal_retrieval_receipt


def test_copilot_quality_gate_all_traps_evaluated_and_telemetry(tmp_path: Path):
    """Verify that copilot quality gate evaluates all 10 trap cases without short-circuiting."""
    holdout_path = Path("tests/evals/copilot_golden/v1/sealed_holdout.jsonl")
    receipt_out = tmp_path / "copilot_wave6_feedback_receipt.json"

    exit_code = run_copilot_quality_gate(holdout_path=holdout_path, receipt_out=receipt_out)
    assert receipt_out.is_file()

    receipt_data = json.loads(receipt_out.read_text(encoding="utf-8"))
    assert receipt_data["trap_invocations"] == 10
    assert receipt_data["total_holdout_cases"] == 30

    ablation = receipt_data["ablation_report"]
    # De-rigged evaluation: non-silent trap outputs correctly penalize silence_precision below 1.0
    assert ablation["local_composer_flagged"]["silence_precision"] < 1.0
    assert ablation["local_composer_flagged"]["silence_precision"] == 0.40
    assert ablation["local_composer_flagged"]["groundedness"] >= 0.98


def test_copilot_distractor_assertion_yields_zero_relevance():
    """Verify selecting a distractor assertion yields relevance 0.0 and no citation copying."""
    case = {
        "id": "test_case_relevance_distractor",
        "expected_action": "speak",
        "expected_assertion_ids": ["asrt_real_100"],
        "dialogue": "What is the planned cutover date?",
        "target": "Provide cutover date",
        "speaker": "Alex Rivera",
    }
    # Prediction with distractor assertion
    pred_results = [
        {
            "id": "test_case_relevance_distractor",
            "status": "PASS",
            "response": "Cutover is scheduled for next month.",
            "cited_assertion_ids": ["asrt_distractor_999"],
        }
    ]
    scorecard = compute_copilot_scorecard([case], pred_results)
    # Since distractor is not in expected_assertion_ids, relevance must be 0.0
    assert scorecard.relevance == 0.0
    assert pred_results[0]["cited_assertion_ids"] != case["expected_assertion_ids"]


def test_unclamped_ece_detects_miscalibration():
    """Verify compute_expected_calibration_error detects true calibration gaps without clamping."""
    # Severely overconfident predictions: 95% confidence on false outcomes
    overconfident_confs = [0.95, 0.95, 0.95, 0.95, 0.95]
    zero_actuals = [0.0, 0.0, 0.0, 0.0, 0.0]
    ece_bad = compute_expected_calibration_error(overconfident_confs, zero_actuals)
    # ECE must reflect the ~0.95 error, not masked or clamped
    assert ece_bad >= 0.90

    # Well-calibrated predictions: 80% confidence with 80% accuracy
    calibrated_confs = [0.80, 0.80, 0.80, 0.80, 0.80]
    calibrated_actuals = [1.0, 1.0, 1.0, 1.0, 0.0]
    ece_good = compute_expected_calibration_error(calibrated_confs, calibrated_actuals)
    # Calibration error should be close to 0
    assert ece_good <= 0.05


def test_mcp_judge_v5_7_graph_mutations_and_decision_chain():
    """Verify v5.7 graph_mutations structure is recognized and emits decision_chain and evidence_grade."""
    payload = json.dumps(
        {
            "schema_version": "5.7.0",
            "graph_mutations": [
                {
                    "action": "upsert_node",
                    "node_id": "org:apex_global",
                    "properties": {"name": "Apex Global Enterprises"},
                }
            ],
        }
    )
    raw_res = judge_evaluate_candidate("graph_projection", payload)
    data = json.loads(raw_res)
    assert data["verdict"] == "PASS"
    assert data["confidence_score"] >= 0.85
    assert data["evidence_grade"] in ("A", "B")
    assert isinstance(data["decision_chain"], list)
    assert len(data["decision_chain"]) >= 3
    assert any("graph_projection" in step for step in data["decision_chain"])


def test_mcp_receipt_strict_validation_and_no_case01_synthesis(tmp_path: Path):
    """Verify seal_retrieval_receipt does not synthesize fallback case-01."""
    eval_results = {
        "status": "PASS",
        "all_gates_passed": True,
        "gates": {"real_bge_m3": {"passed": True}},
        "case_results": [{"case_id": "canonical-case-42"}],
    }
    raw = seal_retrieval_receipt(
        json.dumps(eval_results),
        output_dir=str(tmp_path),
        receipt_id="test-e2e-no-case01",
    )
    res = json.loads(raw)
    assert res["status"] == "RECEIPT_SEALED"

    receipt_file = Path(res["receipt_path"])
    receipt_content = receipt_file.read_text(encoding="utf-8")
    assert "canonical-case-42" in receipt_content
    # Confirm case-01 was NOT synthesized
    assert "case-01" not in receipt_content
