"""Unit tests for Wave 7 LLM cost optimization benchmark runner."""
from __future__ import annotations

from dual_pipeline.cost_benchmark_runner import (
    CostBenchmarkResult,
    StageCostDetail,
    calculate_stage_cost,
    execute_e2e_cost_benchmark,
)


def test_calculate_stage_cost_models():
    """Verify exact cost calculation across sol, luna, flash, and default tiers."""
    # GPT-5.6-sol ($15 in / $60 out per M)
    cost_sol = calculate_stage_cost("gpt-5.6-sol", 1_000_000, 1_000_000)
    assert cost_sol == 75.0

    # GPT-5.6-luna ($3 in / $12 out per M)
    cost_luna = calculate_stage_cost("gpt-5.6-luna", 1_000_000, 1_000_000)
    assert cost_luna == 15.0
    assert round(cost_luna / cost_sol, 2) == 0.20  # Exactly 80% discount!

    # Gemini 3.8 Flash ($0.10 in / $0.40 out per M)
    cost_flash = calculate_stage_cost("gemini-3.8-flash", 1_000_000, 1_000_000)
    assert cost_flash == 0.50
    assert cost_flash < cost_sol * 0.01  # > 99% discount!


def test_calculate_stage_cost_partial_tokens():
    """Verify precision for fractional token amounts."""
    cost = calculate_stage_cost("gemini-3.8-flash", 10_000, 2_000)
    # 10k in * 0.10/M = 0.001, 2k out * 0.40/M = 0.0008 -> 0.0018
    assert cost == 0.0018


def test_execute_e2e_cost_benchmark_structure():
    """Verify execution of benchmark results with 8 governed stages."""
    result = execute_e2e_cost_benchmark()
    assert isinstance(result, CostBenchmarkResult)
    assert len(result.stages) == 8
    assert result.target_cost_reduction_met is True
    assert result.blended_cost_reduction_pct >= 85.0
    assert result.target_quality_met is True
    assert result.quality_metrics.all_qa_gates_passed is True


def test_stage_cost_details_all_pass():
    """Verify all stages achieve positive cost reduction and pass status."""
    result = execute_e2e_cost_benchmark()
    for stage in result.stages:
        assert isinstance(stage, StageCostDetail)
        assert stage.status == "PASS"
        assert stage.cost_reduction_pct >= 0.0
        assert stage.target_cost_usd <= stage.baseline_cost_usd
