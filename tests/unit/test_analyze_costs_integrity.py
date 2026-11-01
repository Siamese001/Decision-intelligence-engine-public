"""Unit tests for analyze_costs.py integrity, variable scoping, and non-canned telemetry loading."""
from pathlib import Path
import pytest

from analyze_costs import (
    _matches_marker,
    evaluate_cost_economics,
    load_recent_call_receipts,
    load_recent_telemetry_traces,
)


def test_matches_marker_includes_zero_cache():
    """Verify records with cached_tokens == 0 are included, preventing cache-hit-only bias."""
    zero_cache_rec = {
        "timestamp_iso": "2026-10-05T12:00:00Z",
        "cached_tokens": 0,
        "prompt_tokens": 1000,
    }
    assert _matches_marker(zero_cache_rec, "w12-start") is True
    assert _matches_marker(zero_cache_rec, "2026-10-01") is True


def test_matches_marker_rejects_prior_records():
    """Verify timestamp boundary filtering functions correctly."""
    old_rec = {
        "timestamp_iso": "2026-09-30T12:00:00Z",
        "cached_tokens": 500,
    }
    assert _matches_marker(old_rec, "2026-10-01") is False


def test_evaluate_cost_economics_no_unbound_local():
    """Verify evaluate_cost_economics does not raise UnboundLocalError when records exist."""
    metrics = evaluate_cost_economics(since_marker="2026-10-05", allow_benchmark_replay=False)
    assert isinstance(metrics, dict)
    assert "by_workflow" in metrics
    assert "total_calls" in metrics
    assert "blended_cached_token_share_pct" in metrics
    assert "target_met" in metrics


def test_evaluate_cost_economics_no_canned_replay_by_default(tmp_path):
    """Verify that when no records exist, canned benchmark replay is NOT injected by default."""
    metrics = evaluate_cost_economics(since_marker="2099-01-01", allow_benchmark_replay=False)
    assert metrics["total_calls"] == 0
    assert metrics["total_prompt_tokens"] == 0
    assert metrics["blended_cached_token_share_pct"] == 0.0
    assert metrics["target_met"] is False


def test_evaluate_cost_economics_explicit_replay():
    """Verify that canned benchmark replay is available only when explicitly requested."""
    metrics = evaluate_cost_economics(since_marker="2099-01-01", allow_benchmark_replay=True)
    assert metrics["total_calls"] == 7
    assert metrics["total_prompt_tokens"] == 12600
    assert metrics["blended_cached_token_share_pct"] > 50.0
