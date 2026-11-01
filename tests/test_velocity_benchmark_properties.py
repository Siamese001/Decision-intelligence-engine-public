"""
tests/test_velocity_benchmark_properties.py

Hypothesis property-based and adversarial tests for Ingestion Velocity Benchmarking (Wave 6).
Covers boundary payloads, malformed baseline metrics, and token calculation invariants.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.benchmark_ingestion_velocity import (
    PathBenchmarkMetrics,
    estimate_tokens,
    run_all_velocity_benchmarks,
)


@given(text=st.text(min_size=0, max_size=5000))
@settings(max_examples=50)
def test_estimate_tokens_property_monotonic_non_negative(text: str):
    """Token estimation must be non-negative and monotonically non-decreasing with text length."""
    toks = estimate_tokens(text)
    assert toks >= 0
    assert isinstance(toks, int)

    # Prefix property: adding text cannot decrease token count
    longer_text = text + " additional token context"
    assert estimate_tokens(longer_text) >= toks


@given(
    wall_clock_ms=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    llm_calls=st.integers(min_value=0, max_value=100),
    tokens=st.integers(min_value=0, max_value=1000000),
)
@settings(max_examples=30)
def test_path_benchmark_metrics_invariants(wall_clock_ms: float, llm_calls: int, tokens: int):
    """PathBenchmarkMetrics dataclass enforces valid serializability and non-negative counts."""
    m = PathBenchmarkMetrics(
        path_name="property_test_path",
        wall_clock_ms=wall_clock_ms,
        llm_calls=llm_calls,
        prompt_tokens=tokens // 2,
        completion_tokens=tokens - (tokens // 2),
        total_tokens=tokens,
        retries=0,
        status="PASS",
        stage_latencies_ms={"stage_01": 1.0},
        judge_latencies_ms={"judge_01": 1.0},
    )
    d = m.to_dict()
    assert d["wall_clock_ms"] == wall_clock_ms
    assert d["total_tokens"] == tokens
    assert d["llm_calls"] == llm_calls
    assert d["status"] == "PASS"


def test_adversarial_corrupted_baseline_fallback(tmp_path: Path):
    """Corrupted or missing baseline JSON falls back gracefully without raising uncaught exceptions."""
    corrupted_baseline = tmp_path / "corrupted_baseline.json"
    corrupted_baseline.write_text("NOT_VALID_JSON{[[", encoding="utf-8")

    out_json = tmp_path / "velocity_out.json"
    out_table = tmp_path / "perf_table.md"

    # Should fall back to hardcoded realistic baseline constants
    res = run_all_velocity_benchmarks(
        dest_json=out_json,
        dest_table=out_table,
        baseline_json=corrupted_baseline,
    )
    assert res is not None
    assert "baseline_comparison" in res
    assert res["baseline_comparison"]["cold_speedup_vs_baseline"] > 0
    assert out_json.is_file()
    assert out_table.is_file()


def test_boundary_zero_length_transcript(tmp_path: Path):
    """Zero-length transcript file raises SourceIntegrityError immediately."""
    from dual_pipeline.exceptions import SourceIntegrityError

    empty_file = tmp_path / "empty_transcript.md"
    empty_file.write_text("", encoding="utf-8")

    out_json = tmp_path / "velocity_out.json"
    out_table = tmp_path / "perf_table.md"

    with pytest.raises(SourceIntegrityError):
        run_all_velocity_benchmarks(
            dest_json=out_json,
            dest_table=out_table,
            source_transcript=empty_file,
        )
