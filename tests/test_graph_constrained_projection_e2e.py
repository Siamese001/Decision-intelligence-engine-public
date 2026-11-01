"""End-to-End Integration Tests for Graph-Constrained Hybrid Projection Architecture."""

import json
import subprocess
import sys
from pathlib import Path

from dual_pipeline.graph_constrained_projection.engine import GraphConstrainedProjectionEngine


def test_projection_engine_e2e_execution():
    """Verify programmatic end-to-end execution and validation."""
    engine = GraphConstrainedProjectionEngine(project_context="project_lens")
    rows, report, md_table = engine.execute_and_validate()

    assert len(rows) == 9, "Matrix must contain exactly 9 sub-dimension rows (3x3 MECE)"
    assert report.passed is True, "Validation must pass with 0 violations"
    assert len(report.violations) == 0

    dimensions = {r.dimension for r in rows}
    assert len(dimensions) == 3, "Matrix must span 3 strategic dimensions"
    assert "Commercial & Milestone Governance" in dimensions
    assert "Solution Integrity & Technical Realism" in dimensions
    assert "Client Steering & Delivery Transparency" in dimensions

    # Verify Markdown table contains all headers and sub-dimensions
    assert "| Strategic Dimension |" in md_table
    assert "| Coaching for Jen |" in md_table
    assert "Contractual Milestone Defense" in md_table
    assert "Scope Boundary & Extension Discipline" in md_table


def test_cli_runner_e2e(tmp_path: Path):
    """Verify CLI tool execution and artifact emission."""
    python_bin = sys.executable
    out_md = tmp_path / "test_gap_analysis.md"
    out_json = tmp_path / "test_gap_analysis.json"
    receipt_file = tmp_path / "test_projection_receipt.json"

    cmd = [
        python_bin,
        "tools/run_graph_constrained_projection.py",
        "--project",
        "project_lens",
        "--validate",
        "--output-md",
        str(out_md),
        "--output-json",
        str(out_json),
        "--receipt",
        str(receipt_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI runner failed: {proc.stderr}"

    assert out_md.exists() and out_md.stat().st_size > 0
    assert out_json.exists() and out_json.stat().st_size > 0
    assert receipt_file.exists() and receipt_file.stat().st_size > 0

    with open(receipt_file, "r") as f:
        receipt = json.load(f)
    assert receipt["status"] == "COMPLETE"
    assert receipt["terminal_outcome"] == "SUCCESS"
    assert receipt["row_count"] == 9
