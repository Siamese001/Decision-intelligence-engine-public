"""End-to-end verification of incremental ingestion replay and content-addressable differential reuse."""
from __future__ import annotations

from pathlib import Path
import pytest

from tools.benchmark_bge_m3_ingest_replay import run_ingest_replay_benchmark
from dual_pipeline.core import ROOT


@pytest.fixture
def base_graph_path() -> Path:
    cand = ROOT / ".local" / "dual_pipeline" / "generations" / "generation-ed8ef28b770e8c0fde837a2dd72d" / "graph.sqlite"
    if not cand.exists():
        pytest.skip("Base production graph template not found")
    return cand


def test_bge_m3_ingest_replay_e2e(base_graph_path: Path, tmp_path: Path):
    """Verify incremental ingestion replay achieves >= 80% neural savings via differential reuse."""
    report_file = tmp_path / "replay_report.json"
    sandbox_dir = tmp_path / "sandbox"

    res = run_ingest_replay_benchmark(
        base_graph=base_graph_path,
        records_count=30,
        sandbox_dir=sandbox_dir,
        output_report=report_file,
        verbose=False,
    )

    assert res["terminal_outcome"] == "PASS"
    assert report_file.exists()

    # Replay metrics validation
    replay = res["replay_build"]
    assert replay["sidecar_validation"] == "PASS"
    assert replay["neural_savings_rate"] >= 0.80
    assert replay["differential_reused"] > 10
    assert replay["forward_passes"] <= 5
