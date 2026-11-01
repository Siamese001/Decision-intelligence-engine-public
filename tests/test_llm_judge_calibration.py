"""
tests/test_llm_judge_calibration.py

Unit test suite for Wave 1 LLM Judge Calibration & Parameter Wiring.
Verifies calibrated thresholds (6 for standard, 8 for executive) and parameter accessors.
"""
from __future__ import annotations

import pytest
from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge
from dual_pipeline.llm_judge_core.batch_judge import BatchMicroJudgeEvaluator
from dual_pipeline.parameters import (
    get_svp_political_materiality_threshold,
    is_deterministic_judge_prefilter_enabled,
    set_parameter,
    reload_parameters,
)


@pytest.fixture(autouse=True)
def reset_params():
    """Ensure parameters are reset before and after test."""
    reload_parameters()
    yield
    reload_parameters()


def test_parameter_accessors_default():
    """Verify SSOT parameter defaults."""
    assert get_svp_political_materiality_threshold(is_executive=False) == 6
    assert get_svp_political_materiality_threshold(is_executive=True) == 8
    assert is_deterministic_judge_prefilter_enabled() is True


def test_standard_route_accepts_materiality_6():
    """Candidate with materiality 6 passes under calibrated standard threshold (6)."""
    judge = AgenticLeverageJudge(mock=True)
    # Node with senior executive target but brief rationale -> materiality 6
    node = {
        "speaker": "Alice",
        "key_players": ["VP Engineering John Doe"],
        "political_rationale": "Short rationale",
        "networking_wedge": "Short wedge",
    }
    result = judge.evaluate_agentic_leverage(node)
    assert result.political_materiality == 6
    assert result.networking_target_identified is True
    assert result.pass_gate is True
    assert result.rejection_reason is None


def test_executive_route_rejects_materiality_6():
    """Candidate with materiality 6 fails under executive threshold (8)."""
    judge = AgenticLeverageJudge(mock=True)
    node = {
        "speaker": "Alice",
        "key_players": ["VP Engineering John Doe"],
        "political_rationale": "Short rationale",
        "networking_wedge": "Short wedge",
        "route": "executive",
    }
    result = judge.evaluate_agentic_leverage(node)
    assert result.political_materiality == 6
    assert result.pass_gate is False
    assert "below SVP threshold of 8" in (result.rejection_reason or "")


def test_executive_route_accepts_materiality_9():
    """Candidate with materiality 9 passes under executive threshold (8)."""
    judge = AgenticLeverageJudge(mock=True)
    node = {
        "speaker": "Alice",
        "key_players": ["Managing Director Jane Smith"],
        "political_rationale": "Detailed strategic political rationale exceeding thirty characters length",
        "networking_wedge": "High leverage networking wedge exceeding thirty characters length",
        "route": "executive",
    }
    result = judge.evaluate_agentic_leverage(node)
    assert result.political_materiality == 9
    assert result.pass_gate is True
    assert result.rejection_reason is None


def test_subthreshold_node_fails_both_routes():
    """Candidate lacking target (materiality 4) fails standard and executive."""
    judge = AgenticLeverageJudge(mock=True)
    node = {
        "speaker": "Alice",
        "key_players": [],
        "political_rationale": "Rationale",
        "networking_wedge": "Wedge",
    }
    res_std = judge.evaluate_agentic_leverage(node)
    assert res_std.pass_gate is False

    node["route"] = "executive"
    res_exec = judge.evaluate_agentic_leverage(node)
    assert res_exec.pass_gate is False


def test_batch_judge_calibrated_threshold():
    """Batch judge honors calibrated threshold of 6 for standard items."""
    evaluator = BatchMicroJudgeEvaluator(mock=True)
    nodes = [
        {
            "speaker": "Bob",
            "key_players": ["Director Tech Leads"],
            "political_rationale": "Brief note",
            "networking_wedge": "Brief wedge",
        }
    ]
    res = evaluator.evaluate_batch(nodes, reference_text="Meeting transcript")
    assert res.item_count == 1
    assert res.evaluated_items[0]["judgment"]["pass_gate"] is True
    assert res.evaluated_items[0]["judgment"]["political_materiality"] == 6
