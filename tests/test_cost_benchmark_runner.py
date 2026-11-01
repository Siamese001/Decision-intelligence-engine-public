"""tests/test_cost_benchmark_runner.py

Unit tests for canonical MODEL_PRICING_TABLE integration and pinned model Pareto benchmarking
in dual_pipeline/cost_benchmark_runner.py.
"""
from __future__ import annotations

from dual_pipeline.cost_benchmark_runner import (
    PINNED_BENCHMARK_MODELS,
    benchmark_pinned_models_pareto,
    calculate_canonical_stage_cost,
    execute_e2e_cost_benchmark,
)
from dual_pipeline.token_optimization.schemas import MODEL_PRICING_TABLE


def test_canonical_stage_cost_matches_model_pricing_table() -> None:
    """Verify calculate_canonical_stage_cost matches MODEL_PRICING_TABLE with zero drift."""
    for model_id in PINNED_BENCHMARK_MODELS:
        assert model_id in MODEL_PRICING_TABLE
        expected_uncached = MODEL_PRICING_TABLE[model_id].calculate_cost(
            uncached_input_tokens=100_000,
            cached_input_tokens=0,
            output_tokens=20_000,
            is_batch=False,
        )
        actual_uncached = calculate_canonical_stage_cost(model_id, 100_000, 20_000)
        assert actual_uncached == expected_uncached

        expected_batch_cached = MODEL_PRICING_TABLE[model_id].calculate_cost(
            uncached_input_tokens=30_000,
            cached_input_tokens=70_000,
            output_tokens=20_000,
            is_batch=True,
        )
        actual_batch_cached = calculate_canonical_stage_cost(
            model_id, 30_000, 20_000, cached_input_tokens=70_000, is_batch=True
        )
        assert actual_batch_cached == expected_batch_cached


def test_benchmark_pinned_models_pareto_ranking() -> None:
    """Verify benchmark_pinned_models_pareto evaluates all 8 pinned models and sorts by cached cost."""
    pareto = benchmark_pinned_models_pareto(input_tokens=20_000, output_tokens=3_000, cached_prefix_ratio=0.70)
    assert len(pareto) == 8
    # Cheapest should be gemini-3.5-flash-lite
    assert pareto[0]["model_id"] == "gemini-3.5-flash-lite"
    # Verify monotonic sort by prefix_cached_cost_usd
    costs = [float(item["prefix_cached_cost_usd"]) for item in pareto]
    assert costs == sorted(costs)
    # Verify SURGICAL_HEALER default (gpt-6-luna) achieves >= 75% savings vs gpt-6.1-sol
    luna_row = next(item for item in pareto if item["model_id"] == "gpt-6-luna")
    assert float(luna_row["savings_vs_frontier_uncached_pct"]) >= 75.0


def test_execute_e2e_cost_benchmark_passes() -> None:
    """Verify end-to-end cost benchmark structure and >= 85% blended reduction."""
    res = execute_e2e_cost_benchmark()
    assert res.target_cost_reduction_met is True
    assert res.blended_cost_reduction_pct >= 85.0
