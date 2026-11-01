"""End-to-end performance and empirical benchmark test for BGE-M3 on production graph."""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from tools.benchmark_bge_m3_production_graph import run_production_graph_benchmark
from dual_pipeline.core import ROOT


@pytest.fixture
def production_graph_path() -> Path:
    cand = ROOT / ".local" / "dual_pipeline" / "generations" / "generation-ed8ef28b770e8c0fde837a2dd72d" / "graph.sqlite"
    if not cand.exists():
        pytest.skip("Production graph snapshot not found on local filesystem")
    return cand


def test_bge_m3_production_graph_benchmark_e2e(production_graph_path: Path, tmp_path: Path):
    """Verify cold vs warm differential performance on production graph corpus."""
    report_file = tmp_path / "benchmark_report.json"
    sandbox_dir = tmp_path / "sandbox"

    res = run_production_graph_benchmark(
        graph_path=production_graph_path,
        records_limit=40,
        sandbox_dir=sandbox_dir,
        output_report=report_file,
        verbose=False,
    )

    assert res["terminal_outcome"] == "PASS"
    assert res["records_evaluated"] > 0
    assert report_file.exists()

    # 1. Cold build integrity
    cold = res["cold_build"]
    assert cold["sidecar_validation"] == "PASS"
    assert cold["throughput_records_per_sec"] > 5.0
    min_norm, max_norm = cold["dense_norm_range"]
    assert 0.85 <= min_norm <= 1.15
    assert 0.85 <= max_norm <= 1.15

    # 2. Warm build speedup and differential reuse
    warm = res["warm_differential_build"]
    assert warm["sidecar_validation"] == "PASS"
    assert warm["speedup_ratio"] >= 2.0
    assert warm["differential_reuse_rate"] >= 0.80
    w_min, w_max = warm["dense_norm_range"]
    assert 0.85 <= w_min <= 1.15
    assert 0.85 <= w_max <= 1.15
