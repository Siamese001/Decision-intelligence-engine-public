"""Adversarial and boundary regression tests for Wave 3: Copilot, Multi-Judge Calibration, and MCP Rubrics."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from evals.copilot_quality_gate import (
    _evaluate_case_frames,
    _evaluate_case_composer,
    HOLDOUT_ASSERTIONS,
)
from evals.copilot_scorecard import compute_copilot_scorecard
from dual_pipeline.live_meeting_recorder_core.copilot_composer import LocalCopilotComposer
from evals.offline_hitl_calibration_runner import (
    OfflineHITLCalibrationRunner,
)
from tools.mcp_judge_eval_core.rubrics import judge_evaluate_candidate
from tools.mcp_judge_eval_core.receipt import seal_retrieval_receipt


def test_adversarial_trap_case_leaking_commentary_penalized():
    """Verify that a trap case producing non-empty commentary fails closed."""
    trap_case = {
        "id": "adversarial_trap_01",
        "expected_action": "trap",
        "expected_assertion_ids": [],
        "dialogue": "Let's see if the copilot hallucinates when we ask an ungrounded question.",
        "target": "Remain silent",
        "speaker": "Observer",
    }
    # Simulate leaky copilot outputting speculative text
    leaky_results = [
        {
            "id": "adversarial_trap_01",
            "status": "FAIL",  # Because rendered_count > 0 on trap
            "response": "Here is an ungrounded guess about future roadmap plans.",
            "cited_assertion_ids": [],
        }
    ]
    scorecard = compute_copilot_scorecard([trap_case], leaky_results)
    # Silence precision should be 0.0 because it spoke on a trap moment
    assert scorecard.silence_precision == 0.0


def test_adversarial_rogue_hitl_feedback_poisoning_fails_closed():
    """Verify that rogue votes attempting to flip the consensus winner fail closed."""
    runner = OfflineHITLCalibrationRunner()
    # 25 rogue votes vs 5 authentic votes will flip the winner and fail stability check
    stability_res = runner.test_adversarial_stability(
        authentic_count=5,
        rogue_count=25,
    )
    assert stability_res["winner_flipped"] is True
    assert stability_res["poisoning_contained"] is False


def test_adversarial_mcp_receipt_empty_and_missing_fields(tmp_path: Path):
    """Verify seal_retrieval_receipt raises ValueError on empty or missing mandatory fields."""
    # 1. Completely empty JSON object
    with pytest.raises(ValueError, match="Evaluation results cannot be empty"):
        seal_retrieval_receipt("{}", output_dir=str(tmp_path))

    # 2. Case in case_results missing case_id
    with pytest.raises(ValueError, match="missing required non-empty 'case_id'"):
        seal_retrieval_receipt(
            json.dumps({"case_results": [{"metric": "foo"}]}),
            output_dir=str(tmp_path),
        )

    # 3. Empty string case_id
    with pytest.raises(ValueError, match="missing required non-empty 'case_id'"):
        seal_retrieval_receipt(
            json.dumps({"case_results": [{"case_id": "   "}]}),
            output_dir=str(tmp_path),
        )

    # 4. Empty prompt_sha256 in receipt payload
    with pytest.raises(ValueError, match="Empty 'prompt_sha256'"):
        seal_retrieval_receipt(
            json.dumps({"prompt_sha256": "", "case_results": [{"case_id": "c1"}]}),
            output_dir=str(tmp_path),
        )


def test_adversarial_mcp_judge_c19_and_secret_egress():
    """Verify judge_evaluate_candidate rejects C-19 career leverage and credential leaks."""
    # C-19 marker
    c19_res = judge_evaluate_candidate(
        "governance_compliance",
        "Executive promotion trajectory with $1.2M personal leverage target.",
    )
    c19_data = json.loads(c19_res)
    assert c19_data["verdict"] == "FAIL"
    assert c19_data["confidence_score"] == 0.0
    assert any("Rule C-19" in e for e in c19_data["errors"])

    # AWS Secret pattern
    secret_res = judge_evaluate_candidate(
        "governance_compliance",
        "Deploying service with AWS key AKIA_REDACTED_EXAMPLE to cloud.",
    )
    secret_data = json.loads(secret_res)
    assert secret_data["verdict"] == "FAIL"
    assert secret_data["confidence_score"] == 0.0
    assert any("Credential leak" in e for e in secret_data["errors"])


def test_adversarial_legacy_v5_7_graph_mutations_regression():
    """Verify v5.7 graph_mutations structure passes graph_projection rubric instead of 0.0."""
    valid_v5_7_payload = json.dumps(
        {
            "schema_version": "5.7.0",
            "graph_mutations": [
                {
                    "action": "upsert_node",
                    "node_id": "system:core_service",
                    "properties": {"tier": "L0"},
                }
            ],
        }
    )
    raw = judge_evaluate_candidate("graph_projection", valid_v5_7_payload)
    data = json.loads(raw)
    assert data["verdict"] == "PASS"
    assert data["confidence_score"] >= 0.85
    assert not any("Invalid projection structure" in e for e in data["errors"])


def test_adversarial_svp_leverage_rubric_evaluates_framing():
    """Verify svp_leverage rubric evaluates strategic alignment and rejects non-strategic text."""
    # Non-strategic text
    weak_res = judge_evaluate_candidate(
        "svp_leverage",
        "We updated the button styling and changed the background color.",
    )
    weak_data = json.loads(weak_res)
    assert weak_data["verdict"] != "PASS"
    assert any("Candidate lacks strategic" in e for e in weak_data["errors"])

    # Strategic executive text
    strong_res = judge_evaluate_candidate(
        "svp_leverage",
        "Strategic executive decision on portfolio governance and architectural milestone impact.",
    )
    strong_data = json.loads(strong_res)
    assert strong_data["verdict"] == "PASS"
    assert strong_data["confidence_score"] >= 0.85
