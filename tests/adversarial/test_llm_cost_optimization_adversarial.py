"""Adversarial and edge-case testing for LLM cost benchmark runner."""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

from dual_pipeline.cost_benchmark_runner import (
    CostBenchmarkResult,
    QualityMetricsComparison,
    StageCostDetail,
    calculate_stage_cost,
    execute_e2e_cost_benchmark,
    generate_benchmark_receipt_and_report,
)


def test_adversarial_boundary_token_payloads():
    """Test boundary payloads: zero tokens, massive 100M token inputs, extreme ratios."""
    # Zero tokens
    zero_cost = calculate_stage_cost("gemini-3.8-flash", 0, 0)
    assert zero_cost == 0.0

    # 100 Million tokens
    huge_cost = calculate_stage_cost("gpt-5.6-sol", 100_000_000, 100_000_000)
    assert huge_cost == 7500.0

    # Extremely asymmetrical ratio (1M input, 0 output)
    input_only = calculate_stage_cost("gpt-5.6-luna", 1_000_000, 0)
    assert input_only == 3.0


def test_adversarial_unknown_model_fallback():
    """Test that unrecognized model identifiers safely fall back to standard governed pricing."""
    fallback_cost = calculate_stage_cost("unknown-model-xyz", 1_000_000, 1_000_000)
    # Default is $5 in / $25 out -> $30
    assert fallback_cost == 30.0


def test_adversarial_invariant_bypass_reduction_check():
    """Verify that benchmark cannot report target_cost_reduction_met=True if reduction is under 85%."""
    # Simulate failed reduction (e.g. 50% reduction)
    stage = StageCostDetail(
        stage_id="MOCK",
        stage_name="Mock Stage",
        baseline_model="gpt-5.6-sol",
        target_model="gpt-5.6-sol",
        input_tokens=1000,
        output_tokens=1000,
        baseline_cost_usd=0.075,
        target_cost_usd=0.075,
        cost_reduction_pct=0.0,
        latency_ms=100.0,
        status="PASS",
    )
    result = CostBenchmarkResult(
        benchmark_id="bench-fail",
        reference_meeting="Mock",
        timestamp="2026-09-27T00:00:00Z",
        stages=[stage],
        total_baseline_cost_usd=0.075,
        total_target_cost_usd=0.075,
        blended_cost_reduction_pct=0.0,
        quality_metrics=QualityMetricsComparison(),
        target_cost_reduction_met=False,
        target_quality_met=True,
    )

    tmp_dir = Path("/tmp/mock_bench_test")
    tmp_receipt = tmp_dir / "receipt.json"
    tmp_report = tmp_dir / "report.md"

    try:
        generate_benchmark_receipt_and_report(result, tmp_receipt, tmp_report)
        receipt_data = json.loads(tmp_receipt.read_text(encoding="utf-8"))
        assert receipt_data["status"] == "FAIL"
        assert receipt_data["target_cost_reduction_met"] is False
    finally:
        if tmp_receipt.exists():
            tmp_receipt.unlink()
        if tmp_report.exists():
            tmp_report.unlink()
        if tmp_dir.exists():
            tmp_dir.rmdir()


def test_adversarial_concurrent_benchmark_execution(tmp_path: Path):
    """Test concurrent benchmark runs and report generations to prevent file corruption."""
    def worker(idx: int) -> bool:
        res = execute_e2e_cost_benchmark()
        rec_path = tmp_path / f"receipt_{idx}.json"
        rep_path = tmp_path / f"report_{idx}.md"
        generate_benchmark_receipt_and_report(res, rec_path, rep_path)
        return rec_path.is_file() and rep_path.is_file()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(16)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(results)
    assert len(results) == 16
