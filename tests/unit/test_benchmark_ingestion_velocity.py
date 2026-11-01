"""
Unit tests for Ingestion Velocity Benchmarking Harness (Wave 3).
Validates empirical metrics collection, token accounting, stage isolation,
and receipt serialization across cold, cached, and selective paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tools.benchmark_ingestion_velocity import (
    PathBenchmarkMetrics,
    benchmark_cached_path,
    benchmark_cold_path,
    benchmark_selective_regeneration_path,
    estimate_tokens,
    run_all_velocity_benchmarks,
)


@pytest.fixture
def sample_payload() -> tuple[str, str, Dict[str, Any]]:
    f1 = (
        "### [00:00:10] Taylor Mercer: Reviewing system integration requirements.\n"
        "### [00:01:25] Amit Ayer: Enterprise SSO and VPC networking configurations are underway.\n"
    )
    f2 = (
        "# Executive Strategic Brief\n\n"
        "## Meeting Objective\nAlignment on enterprise platform integration.\n\n"
        "## Key Discussion Points & Findings\n* Enterprise SSO configurations.\n\n"
        "## Action & Governance Ledger\n| Action | Owner | Timing | Evidence |\n"
        "| :--- | :--- | :--- | :--- |\n| Deploy VPC | Amit Ayer | Q4 | Turn 2 |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Amit Ayer\n#### Perspective\nInfrastructure integration focus.\n"
    )
    f3 = {
        "meeting_id": "velocity_test_meeting",
        "evidence_index": {
            "ev-01": {"locator": {"kind": "timestamp", "value": "00:00:10"}, "text": "system integration"},
            "ev-02": {"locator": {"kind": "timestamp", "value": "00:01:25"}, "text": "Enterprise SSO"},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:sso", "name": "SSO", "mutation": "create"}
            ],
            "proposal_updates": [
                {
                    "proposal_id": "prop-01",
                    "name": "SSO Integration",
                    "description": "Enterprise SSO rollout",
                    "evidence_ids": ["ev-02"],
                }
            ],
        },
        "incremental_reconciliation": {"new_entities": 1, "confirmed_entities": 0},
    }
    return f1, f2, f3


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    toks = estimate_tokens("hello world this is a test")
    assert toks > 0
    assert toks == int(6 * 1.3 + 26 * 0.05)


def test_cold_path_metrics(sample_payload, tmp_path: Path):
    f1, f2, f3 = sample_payload
    metrics = benchmark_cold_path(f1, f2, f3, tmp_path)

    assert metrics.path_name == "cold_path"
    assert metrics.wall_clock_ms > 0
    assert metrics.llm_calls == 3
    assert metrics.prompt_tokens > 0
    assert metrics.completion_tokens > 0
    assert metrics.total_tokens == metrics.prompt_tokens + metrics.completion_tokens
    assert metrics.retries == 0
    assert metrics.status == "PASS"
    assert "stage_01_transcript" in metrics.stage_latencies_ms
    assert "stage_02_recap" in metrics.stage_latencies_ms
    assert "stage_03_graph" in metrics.stage_latencies_ms
    assert "deterministic_qa_gates" in metrics.stage_latencies_ms
    assert "decomposed_judges" in metrics.judge_latencies_ms
    assert metrics.judge_latencies_ms["decomposed_judges"] > 0


def test_cached_path_bypasses_llm(sample_payload, tmp_path: Path):
    f1, f2, f3 = sample_payload
    # Prime cold path first so attempt seals exist
    benchmark_cold_path(f1, f2, f3, tmp_path)

    cached_metrics = benchmark_cached_path(f1, f2, f3, tmp_path)
    assert cached_metrics.path_name == "cached_path"
    assert cached_metrics.status == "PASS"
    assert cached_metrics.llm_calls == 0
    assert cached_metrics.prompt_tokens == 0
    assert cached_metrics.completion_tokens == 0
    assert cached_metrics.total_tokens == 0
    assert cached_metrics.retries == 0
    assert cached_metrics.stage_latencies_ms["stage_01_transcript"] == 0.0
    assert cached_metrics.stage_latencies_ms["stage_02_recap"] == 0.0
    assert cached_metrics.stage_latencies_ms["stage_03_graph"] == 0.0
    assert cached_metrics.judge_latencies_ms["decomposed_judges"] == 0.0


def test_selective_regeneration_path(sample_payload, tmp_path: Path):
    f1, f2, f3 = sample_payload
    # Prime cold path first
    benchmark_cold_path(f1, f2, f3, tmp_path)

    sel_metrics = benchmark_selective_regeneration_path(f1, f2, f3, tmp_path)
    assert sel_metrics.path_name == "selective_regeneration_path"
    assert sel_metrics.status == "PASS"
    assert sel_metrics.llm_calls == 1
    assert sel_metrics.retries == 1
    assert sel_metrics.prompt_tokens > 0
    assert sel_metrics.total_tokens < estimate_tokens(f1) + estimate_tokens(f2) + estimate_tokens(json.dumps(f3))
    assert sel_metrics.stage_latencies_ms["stage_01_transcript"] == 0.0
    assert sel_metrics.stage_latencies_ms["stage_02_recap"] == 0.0
    assert sel_metrics.stage_latencies_ms["stage_03_graph"] > 0
    assert sel_metrics.judge_latencies_ms["decomposed_judges"] > 0


def test_run_all_velocity_benchmarks(tmp_path: Path):
    json_out = tmp_path / "velocity_benchmark_results.json"
    table_out = tmp_path / "before_after_performance_table.md"
    sandbox = tmp_path / "sandbox"

    res = run_all_velocity_benchmarks(
        dest_json=json_out,
        dest_table=table_out,
        sandbox_dir=sandbox,
    )

    assert json_out.exists()
    assert table_out.exists()

    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert "benchmark_timestamp" in data
    assert "paths" in data
    assert "velocity_improvements" in data

    improvements = data["velocity_improvements"]
    assert improvements["cached_speedup_factor"] > 0.0
    assert improvements.get("cached_speedup_vs_baseline", 1.0) >= 1.0
    assert improvements["cached_token_savings_pct"] == 100.0
    assert improvements["selective_llm_call_reduction_pct"] == 66.7

    md_content = table_out.read_text(encoding="utf-8")
    assert "Before vs. After Performance Table" in md_content
    assert "Empirical Performance Benchmark Matrix" in md_content
    assert "Quantified Velocity Gains" in md_content
    assert "Unresolved Bottlenecks & Concise RCA" in md_content
    assert "Frontier Model Socket Latency" in md_content
