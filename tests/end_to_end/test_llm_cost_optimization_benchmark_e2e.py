"""End-to-end integration test for Wave 7 cost benchmark runner and report generator."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.cost_benchmark_runner import (
    execute_e2e_cost_benchmark,
    generate_benchmark_receipt_and_report,
)


def test_llm_cost_optimization_benchmark_lifecycle_e2e(tmp_path: Path):
    """E2E lifecycle test verifying receipt and report creation, schema, and threshold checks."""
    receipt_file = tmp_path / "test_benchmark_receipt.json"
    report_file = tmp_path / "test_benchmark_comparison.md"

    # Step 1: Run benchmark
    result = execute_e2e_cost_benchmark(
        reference_meeting="Google_Alex_Shreyak_Amit_Anya",
        reference_date="09/25/2026",
    )

    # Verify benchmark result
    assert result.target_cost_reduction_met is True
    assert result.blended_cost_reduction_pct >= 85.0
    assert result.total_target_cost_usd < result.total_baseline_cost_usd

    # Step 2: Generate deliverables
    generate_benchmark_receipt_and_report(
        benchmark_result=result,
        receipt_path=receipt_file,
        markdown_path=report_file,
    )

    # Step 3: Verify receipt JSON on disk
    assert receipt_file.is_file()
    receipt_json = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt_json["wave"] == 7
    assert receipt_json["status"] == "COMPLETE"
    assert receipt_json["target_cost_reduction_met"] is True
    assert len(receipt_json["stages"]) == 8

    # Step 4: Verify markdown report on disk
    assert report_file.is_file()
    report_md = report_file.read_text(encoding="utf-8")
    assert "LLM Cost Optimization" in report_md
    assert "Blended Cost Reduction" in report_md
    assert "D01 Forensic Anchor" in report_md
    assert "D02 Modular 3-Pass DAG" in report_md
    assert "D03 Position-Pointer Quotes" in report_md
