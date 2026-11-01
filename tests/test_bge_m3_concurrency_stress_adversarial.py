"""Adversarial and concurrency stress verification for BGE-M3 embedding runtime.

Covers edge-case categories:
1. Concurrency & race: 16-worker parallel SQLite WAL reads/writes and embedding inference.
2. Boundary payload: Empty strings, 1-character tokens, and whitespace inputs in concurrent batches.
3. Overflow & exhaustion: Burst worker execution without database lock timeouts or memory leakage.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.benchmark_bge_m3_concurrency_stress import run_concurrency_stress_benchmark
from dual_pipeline.core import ROOT


def test_bge_m3_concurrency_stress_adversarial(tmp_path: Path):
    """Verify 16-worker concurrency stress with 0 lock timeouts and bounded memory."""
    report_file = tmp_path / "stress_report.json"
    sandbox_dir = tmp_path / "sandbox"

    res = run_concurrency_stress_benchmark(
        num_workers=16,
        num_requests=40,
        sandbox_dir=sandbox_dir,
        output_report=report_file,
        verbose=False,
    )

    assert res["terminal_outcome"] == "PASS"
    assert res["workers_configured"] == 16
    assert res["requests_executed"] == 40
    assert res["lock_errors_count"] == 0
    assert report_file.exists()

    # Latency percentiles sanity
    lats = res["latencies_ms"]
    assert lats["p50"] > 0
    assert lats["max"] >= lats["p50"]

    # Memory resilience
    mem = res["memory_profile"]
    assert not mem["leak_detected"]
    assert mem["delta_mb"] < 50.0


def test_bge_m3_concurrency_stress_boundary_payloads(tmp_path: Path):
    """Verify concurrent worker inference handles empty, whitespace, and single-char payloads."""
    from dual_pipeline.index_core.backends import get_backend
    import concurrent.futures

    backend = get_backend("bge-m3-dev", allow_dev_backend=True)

    boundary_payloads = [
        "",
        " ",
        "   ",
        "a",
        "\n\t",
        "normal query for boundary test",
    ]

    def _worker(text: str):
        return backend.encode_many_dense_only([text])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, p) for p in boundary_payloads]
        for f in concurrent.futures.as_completed(futures):
            vecs = f.result()
            assert len(vecs) == 1
            assert len(vecs[0]) == 1024


def test_bge_m3_concurrency_stress_receipt_integrity():
    """Verify durable Wave 4 receipt recorded in repo meets strict concurrency invariants."""
    receipt_path = ROOT / "reports" / "benchmarks" / "bge_m3_concurrency_stress_receipt.json"
    if not receipt_path.exists():
        pytest.skip("Wave 4 concurrency receipt not found in reports/benchmarks")

    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert data["terminal_outcome"] == "PASS"
    assert data["workers_configured"] >= 16
    assert data["requests_executed"] >= 200
    assert data["lock_errors_count"] == 0
    assert not data["memory_profile"]["leak_detected"]
