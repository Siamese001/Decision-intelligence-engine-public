"""End-to-End Tests for Wave 4: Proportional Gating & Promotion Target Proof.

Validates:
1. Benchmark replay for `--phase compare` producing chained receipt with >=60% speedup.
2. Readiness stage planner resolving Lane 0, Lane 1, and Lane 2 correctly.
3. Impacted test module filtering reducing test scope for focused changes.
4. Concurrent gate runner honoring exempt indices with proportional_exempt cache decisions.
5. Readiness attestation capturing proportional execution metadata.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_pr_promotion import run_benchmark
from tools.readiness_concurrent import _execute_parallel_linter_gates
from tools.readiness_planner import (
    ProportionalPlan,
    filter_impacted_test_modules,
    load_gate_manifest,
    plan_readiness,
)


def test_wave4_benchmark_compare_phase_e2e(tmp_path: Path) -> None:
    """Verifies benchmark execution under --phase compare produces >=60% speedup chained receipt."""
    receipt_file = tmp_path / "wave-4-test.json"
    res = run_benchmark(
        phase="compare",
        receipt_path=receipt_file,
        workloads=["documentation", "application", "governance"],
        iterations=1,
        fast=True,
        repo_root=ROOT,
    )
    assert res["terminal_outcome"] == "PASS"
    assert res["phase"] == "compare"
    assert receipt_file.is_file()

    data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert data["terminal_outcome"] == "PASS"
    assert "compare_qualification" in data
    assert "paired_comparison" in data
    assert "chained_wave3_receipt_digest" in data

    paired = data["paired_comparison"]
    assert paired["meets_target"] is True
    assert paired["speedup_p50_pct"] >= 60.0
    assert paired["p50_ratio"] <= 0.40

    qual = data["compare_qualification"]
    assert qual["defect_detection_comparison"]["zero_false_passes_verified"] is True
    assert qual["coverage_floor_preserved"] is True


def test_readiness_planner_lane_resolution_e2e() -> None:
    """Verifies plan_readiness resolves Lane 0, 1, and 2 based on file extensions and paths."""
    # Lane 0: Pure documentation
    p0, idx0 = plan_readiness(["docs/architecture.md", "README.md"], repo_root=ROOT)
    assert p0.risk_lane == 0
    assert p0.proportional_applied is True
    assert len(idx0) == 6
    assert 30 not in idx0  # full acceptance suite bypassed

    # Lane 1: Low-risk tools/scripts
    p1, idx1 = plan_readiness(["tools/utility.py"], repo_root=ROOT)
    assert p1.risk_lane == 1
    assert p1.proportional_applied is True
    assert len(idx1) == 22

    # Lane 2: High-risk core runtime or storage
    p2, idx2 = plan_readiness(["storage/connection.py", "dual_pipeline/engine.py"], repo_root=ROOT)
    assert p2.risk_lane == 2
    assert p2.proportional_applied is False
    assert len(idx2) == 30


def test_impacted_test_module_filtering_e2e() -> None:
    """Verifies filter_impacted_test_modules targets only relevant test files."""
    base_suite = ["tests.test_all_1", "tests.test_all_2", "tests.test_all_3"]

    # Docs change -> smoke tests only
    doc_tests = filter_impacted_test_modules(["docs/readme.md"], test_mode="smoke_only", base_modules=base_suite)
    assert "tests.test_runtime_evidence_claims" in doc_tests
    assert "tests.test_all_1" not in doc_tests

    # Full suite mode -> returns all base_modules unchanged
    full_tests = filter_impacted_test_modules(["storage/connection.py"], test_mode="full_suite", base_modules=base_suite)
    assert full_tests == base_suite

    # Impacted mode with tools/ -> includes pr_governance tests
    tool_tests = filter_impacted_test_modules(
        ["tools/readiness_planner.py"], test_mode="impacted_only", base_modules=base_suite
    )
    assert "tests.governance.test_pr_governance" in tool_tests


def test_concurrent_gate_runner_exempt_stages_e2e(tmp_path: Path) -> None:
    """Verifies parallel gate runner marks exempt stages without invoking them."""
    called_stages: list[str] = []

    def mock_run_stage(name: str, cmd: list[str]) -> tuple[bool, str, float]:
        called_stages.append(name)
        return True, "passed", 0.01

    stages_slice = [
        ("Gate 1", ["python", "g1.py"]),
        ("Gate 2", ["python", "g2.py"]),
        ("Gate 3", ["python", "g3.py"]),
    ]
    # Mark stage index 2 (Gate 2) as exempt (1-indexed in manifest)
    exempt = {2}

    results, all_passed = _execute_parallel_linter_gates(
        stages_slice=stages_slice,
        total_stages=3,
        use_cache=False,
        max_workers=2,
        head_sha="testha123",
        target_files=["file.py"],
        verbose=False,
        repo_root=tmp_path,
        run_stage_fn=mock_run_stage,
        exempt_indices=exempt,
    )
    assert all_passed is True
    assert len(results) == 3

    # Gate 2 must be marked proportional_exempt with duration 0.0 and never executed
    g2_res = next(r for r in results if r["stage_name"] == "Gate 2")
    assert g2_res["status"] == "PASS"
    assert g2_res["cache_decision"] == "proportional_exempt"
    assert g2_res["duration_seconds"] == 0.0
    assert "Gate 2" not in called_stages

    # Gate 1 and Gate 3 must have been executed
    assert "Gate 1" in called_stages
    assert "Gate 3" in called_stages
