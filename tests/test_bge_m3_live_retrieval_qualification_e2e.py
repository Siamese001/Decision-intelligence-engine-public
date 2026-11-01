"""End-to-end verification of 11-gate BGE-M3 retrieval qualification and query batch pre-warming."""
from __future__ import annotations

from pathlib import Path
import pytest

from tools.benchmark_bge_m3_retrieval_gates import run_retrieval_gates_benchmark
from dual_pipeline.core import ROOT


@pytest.fixture
def runtime_root() -> Path:
    rt = ROOT / ".local" / "dual_pipeline"
    if not (rt / "active_pair.json").is_file():
        pytest.skip("Active pair not found under runtime root")
    return rt


def test_bge_m3_live_retrieval_qualification_e2e(runtime_root: Path, tmp_path: Path):
    """Verify all 11 gates pass with mathematical parity and query batch speedup."""
    report_file = tmp_path / "retrieval_gates_report.json"

    res = run_retrieval_gates_benchmark(
        runtime_root=runtime_root,
        output_report=report_file,
        verbose=False,
    )

    assert res["terminal_outcome"] == "PASS"
    assert report_file.exists()

    # 1. Query Batch Pre-Warming Benchmarks
    q_bench = res["query_batch_profiling"]
    assert q_bench["queries_count"] >= 10
    assert q_bench["speedup_ratio"] >= 2.0
    assert q_bench["zero_quality_loss"] is True
    assert q_bench["cosine_similarity_min"] >= 0.99999

    # 2. 11-Gate Retrieval Qualification
    gates_audit = res["retrieval_qualification_11_gates"]
    assert gates_audit["all_11_gates_passed"] is True
    assert gates_audit["terminal_outcome"] == "PASS"

    gates = gates_audit["gates"]
    assert len(gates) == 11
    for gate_name, status in gates.items():
        assert status is True, f"Gate {gate_name} failed verification"
