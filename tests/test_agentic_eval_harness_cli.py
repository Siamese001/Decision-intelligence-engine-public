"""
Unit and acceptance test suite for Wave 5: Unified Agentic Evaluation CLI & Pareto Frontier Analyzer.

Verifies:
- ParetoFrontierAnalyzer dominance sorting and non-dominated frontier extraction.
- RegressionVerdict detection on quality drops (>5%) and latency spikes (>25%).
- Complete execution of tools.run_agentic_eval_harness.run_agentic_evaluation_suite.
- RFC-8259 sealed receipt emission and digest calculation.
"""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.evals.pareto import (
    ParetoFrontierAnalyzer,
    ParetoPoint,
)
from tools.run_agentic_eval_harness import run_agentic_evaluation_suite


def test_pareto_frontier_dominance():
    """Verify ParetoFrontierAnalyzer correctly sorts dominated vs non-dominated points."""
    analyzer = ParetoFrontierAnalyzer()

    p1 = ParetoPoint("p1_fast", quality_score=80.0, latency_ms=100.0, cost_microcredits=10.0, reasoning_effort="none")
    p2 = ParetoPoint("p2_accurate", quality_score=95.0, latency_ms=500.0, cost_microcredits=50.0, reasoning_effort="medium")
    p3 = ParetoPoint("p3_dominated", quality_score=75.0, latency_ms=600.0, cost_microcredits=60.0, reasoning_effort="none")

    analyzer.compute_frontier([p1, p2, p3])

    # p1 is fastest and cheapest -> optimal
    assert p1.is_pareto_optimal
    assert len(p1.dominated_by) == 0

    # p2 has highest quality -> optimal
    assert p2.is_pareto_optimal
    assert len(p2.dominated_by) == 0

    # p3 is strictly worse than p1 and p2 in quality, latency, and cost -> dominated
    assert not p3.is_pareto_optimal
    assert len(p3.dominated_by) >= 1


def test_pareto_regression_detection():
    """Verify regression detection on quality drops and latency spikes."""
    analyzer = ParetoFrontierAnalyzer(max_quality_drop_pct=5.0, max_latency_spike_pct=25.0)

    baseline = ParetoPoint("base", quality_score=90.0, latency_ms=1000.0, cost_microcredits=100.0, reasoning_effort="medium")

    # Nominal acceptable variation (quality +1, latency +5%)
    nominal = ParetoPoint("cand_ok", quality_score=91.0, latency_ms=1050.0, cost_microcredits=102.0, reasoning_effort="medium")
    verdict_nominal = analyzer.evaluate_regression(nominal, baseline)
    assert not verdict_nominal.is_regression
    assert len(verdict_nominal.reasons) == 0

    # Significant quality drop (-8 pts)
    degraded = ParetoPoint("cand_bad_q", quality_score=82.0, latency_ms=1000.0, cost_microcredits=100.0, reasoning_effort="medium")
    verdict_degraded = analyzer.evaluate_regression(degraded, baseline)
    assert verdict_degraded.is_regression
    assert any("Quality dropped" in r for r in verdict_degraded.reasons)

    # Significant latency spike (+40%)
    slow = ParetoPoint("cand_slow", quality_score=90.0, latency_ms=1400.0, cost_microcredits=100.0, reasoning_effort="medium")
    verdict_slow = analyzer.evaluate_regression(slow, baseline)
    assert verdict_slow.is_regression
    assert any("Latency increased" in r for r in verdict_slow.reasons)


def test_e2e_agentic_eval_harness_suite(tmp_path: Path):
    """Verify execution of full 5-tier evaluation pyramid emitting sealed receipt."""
    receipt_out = tmp_path / "test_agentic_receipt.json"

    result = run_agentic_evaluation_suite(
        output_receipt_path=receipt_out, update_ledger=False
    )

    assert result["terminal_verdict"] == "PASS"
    assert result["tiers_executed"] == 7
    assert "receipt_digest" in result
    assert len(result["receipt_digest"]) == 64

    # Check that file exists on disk and matches returned data
    assert receipt_out.exists()
    disk_data = json.loads(receipt_out.read_text(encoding="utf-8"))
    assert disk_data["receipt_digest"] == result["receipt_digest"]
    assert "tier_1_trajectory" in disk_data["results"]
    assert "tier_2_calibration" in disk_data["results"]
    assert "tier_3_sandbox" in disk_data["results"]
    assert "tier_4_adversarial" in disk_data["results"]
    assert "tier_5_pareto" in disk_data["results"]
