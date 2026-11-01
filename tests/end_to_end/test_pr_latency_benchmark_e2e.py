"""
tests/end_to_end/test_pr_latency_benchmark_e2e.py

End-to-end tests for Wave 1 PR promotion latency measurement and timeline telemetry.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tools.benchmark_pr_promotion import run_benchmark
from tools.pr_governance_core.diagnostics import (
    compute_bounded_error_tail,
    extract_failure_ids,
    format_failed_stage_diagnostics,
)
from tools.pr_governance_core.latency import PROMOTION_PHASES, PromotionTimeline


@pytest.mark.e2e
def test_benchmark_baseline_execution_and_receipt_emission() -> None:
    with tempfile.TemporaryDirectory(prefix="e2e_bench_") as temp_dir:
        receipt_path = Path(temp_dir) / "wave-1-test.json"
        res = run_benchmark(
            phase="baseline",
            receipt_path=receipt_path,
            workloads=("documentation", "application", "governance"),
            iterations=1,
            fast=True,
        )

        assert res["terminal_outcome"] == "PASS"
        assert receipt_path.is_file()

        data = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert data["phase"] == "baseline"
        assert "receipt_digest" in data
        assert len(data["receipt_digest"]) == 64

        # Check all 10 phases in breakdown
        phase_breakdown = data["summary"]["phase_breakdown"]
        for p in PROMOTION_PHASES:
            assert p in phase_breakdown
            assert phase_breakdown[p]["count"] >= 6

        # Check cold vs warm breakdown
        assert "cold" in data["summary"]["cold_vs_warm"]
        assert "warm" in data["summary"]["cold_vs_warm"]

        # Check non-overlapping interval invariant across all runs
        for r in data["runs"]:
            assert r["accounted_phase_seconds"] <= r["wall_clock_seconds"] + 0.05
            assert r["unaccounted_seconds"] >= 0.0


@pytest.mark.e2e
def test_promotion_timeline_ten_phases_accounting() -> None:
    with tempfile.TemporaryDirectory(prefix="e2e_timeline_") as temp_dir:
        timeline = PromotionTimeline(branch="feat/test-telemetry", repo_root=Path(temp_dir))

        for p in PROMOTION_PHASES:
            timeline.checkpoint(p, scope="test_scope", status="PASS")

        timeline.tag_interval("human_wait", 1.5)
        timeline.tag_interval("repairs", 2.0)

        data = timeline.finish(terminal_outcome="PASS")
        assert data["terminal_outcome"] == "PASS"
        assert len(data["phases"]) == len(PROMOTION_PHASES)
        assert data["tagged_intervals"]["human_wait"] == 1.5
        assert data["tagged_intervals"]["repairs"] == 2.0
        assert data["accounted_phase_seconds"] <= data["wall_clock_seconds"] + 0.05


@pytest.mark.e2e
def test_failed_stage_diagnostics_integration() -> None:
    with tempfile.TemporaryDirectory(prefix="e2e_diag_") as temp_dir:
        repo_root = Path(temp_dir)
        stage_output = (
            "Running Module File Budget Linter...\n"
            "FAILED tests/unit/test_sample.py::test_case_one - AssertionError: expected 1 got 0\n"
            "Violation: INV-OUTPUT-MAX-WORDS exceeded: 150 words\n"
            "Traceback (most recent call last):\n"
            "  File 'foo.py', line 12, in <module>\n"
            "ValueError: invalid configuration\n"
        )
        diag = format_failed_stage_diagnostics(
            stage_idx=3,
            stage_name="Module File Budget Linter",
            cmd=["python", "tools/lint_file_budgets.py"],
            output=stage_output,
            repo_root=repo_root,
            branch_slug="test_branch",
        )

        assert "failure_ids" in diag
        assert any("STAGE_3" in fid for fid in diag["failure_ids"])
        assert any("test_case_one" in fid for fid in diag["failure_ids"])
        assert any("INV-OUTPUT-MAX-WORDS" in fid for fid in diag["failure_ids"])

        assert "error_tail" in diag
        assert "ValueError: invalid configuration" in diag["error_tail"]

        assert "full_log_path" in diag
        log_file = repo_root / diag["full_log_path"]
        assert log_file.is_file()
        assert log_file.read_text(encoding="utf-8") == stage_output

        assert "log_digest" in diag
        assert len(diag["log_digest"]) == 64


@pytest.mark.e2e
def test_readiness_planner_32_gate_manifest_parity_and_lanes() -> None:
    from tools.readiness_planner import load_gate_manifest, plan_readiness

    manifest = load_gate_manifest()
    total_stages = manifest.get("total_registered_stages", 32)
    assert total_stages == 32
    assert len(manifest.get("stages", [])) == 32

    # Lane 0: exactly 6 gates
    plan_l0, stages_l0 = plan_readiness(
        target_files=["docs/sample.md"],
        force_full=False,
    )
    assert plan_l0.risk_lane == 0
    assert len(stages_l0) == 6
    assert stages_l0 == [1, 6, 17, 20, 27, 28]

    # Lane 1: exactly 25 gates
    plan_l1, stages_l1 = plan_readiness(
        target_files=["dual_pipeline/some_feature.py"],
        force_full=False,
    )
    assert plan_l1.risk_lane == 1
    assert len(stages_l1) == 25

    # Lane 2: all 32 gates
    plan_l2, stages_l2 = plan_readiness(
        target_files=["tools/verify_main_readiness.py"],
        force_full=False,
    )
    assert plan_l2.risk_lane == 2
    assert len(stages_l2) == 32
    assert 32 in stages_l2
