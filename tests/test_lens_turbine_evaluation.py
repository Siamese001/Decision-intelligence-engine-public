"""End-to-end integration and governance tests for Project LENS Single-Turbine Evaluation."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_execution_emits_valid_report_and_receipt(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports"
    cmd = [
        sys.executable,
        "tools/evaluate_lens_turbine_thresholds.py",
        "--input",
        "tests/fixtures/lens/single_turbine_200_photos.json",
        "--output-dir",
        str(output_dir),
        "--turbine-id",
        "WTG_04",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"CLI runner failed: {res.stderr}"

    report_path = output_dir / "LENS_SINGLE_TURBINE_THRESHOLD_COMPARISON_REPORT.md"
    receipt_path = output_dir / "lens_single_turbine_threshold_comparison_receipt.json"

    assert report_path.exists(), "Markdown report must be emitted"
    assert receipt_path.exists(), "JSON audit receipt must be emitted"

    # Verify report sections
    report_text = report_path.read_text(encoding="utf-8")
    assert "# Project LENS: Single-Turbine Threshold Comparison Report" in report_text
    assert "## 1. Executive Summary & Decision Context" in report_text
    assert "## 2. Governed Denominator Ledger" in report_text
    assert "## 3. Comparative Performance & Trade-Off Matrix" in report_text
    assert "## 4. Itemized Error Register (Relaxed False Acceptances)" in report_text
    assert "## 5. Stage 0 Capture Quality Defect Breakdown" in report_text
    assert "## 6. Governed Statistical Boundaries: N=1 vs. N=50" in report_text

    # Verify key trade-off metrics are present in the report
    assert "30.56%" in report_text  # Strict STP
    assert "88.33%" in report_text  # Relaxed STP
    assert "97.48%" in report_text  # Relaxed Correctness
    assert "2.52%" in report_text   # Relaxed Error rate
    assert "ERR_BLUR" in report_text
    assert "ERR_LIGHTING" in report_text
    assert "ERR_NO_TEXT" in report_text

    # Verify JSON receipt schema and governance flags
    with open(receipt_path, "r", encoding="utf-8") as f:
        receipt_data = json.load(f)

    assert receipt_data["schema_version"] == "1.0.0"
    assert receipt_data["evaluation_status"] == "EVALUATION_COMPLETE"
    assert receipt_data["production_deployment_authorized"] is False
    assert receipt_data["governance_decision"]["fleet_readiness_declared"] is False
    assert receipt_data["governance_decision"]["client_approval_required"] is True
    assert "strict_baseline" in receipt_data["metrics"]
    assert "proposed_relaxed" in receipt_data["metrics"]


def test_reports_directory_generation(tmp_path: Path) -> None:
    target_dir = tmp_path / "reports"
    cmd = [
        sys.executable,
        "tools/evaluate_lens_turbine_thresholds.py",
        "--output-dir",
        str(target_dir),
        "--quiet",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0

    archived_report = target_dir / "LENS_SINGLE_TURBINE_THRESHOLD_COMPARISON_REPORT.md"
    archived_receipt = target_dir / "lens_single_turbine_threshold_comparison_receipt.json"
    assert archived_report.exists(), "Report must exist in output directory"
    assert archived_receipt.exists(), "Receipt must exist in output directory"


def test_module_file_budgets_and_layer_integrity() -> None:
    lens_files = [
        "dual_pipeline/lens_evaluation/models.py",
        "dual_pipeline/lens_evaluation/thresholds.py",
        "dual_pipeline/lens_evaluation/preflight.py",
        "dual_pipeline/lens_evaluation/reconciler.py",
        "dual_pipeline/lens_evaluation/evaluator.py",
        "dual_pipeline/lens_evaluation/reporter.py",
        "tools/evaluate_lens_turbine_thresholds.py",
    ]
    for rel_path in lens_files:
        path = Path(rel_path)
        assert path.exists(), f"File {rel_path} must exist"
        line_count = len(path.read_text().splitlines())
        assert line_count <= 400, f"File {rel_path} has {line_count} lines, exceeding 400-line budget"
