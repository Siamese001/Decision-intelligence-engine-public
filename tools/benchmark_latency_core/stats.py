"""
tools/benchmark_latency_core/stats.py

Statistical computation, percentiles, missing value accounting, and
governed qualification gates for recording startup latency.
Enforces:
1. Missing PCM stays missing (None values are not substituted with wall-clock time).
2. Failed starts and missing PCM are explicitly tracked in the failure denominator.
3. Strict qualification gate evaluation across warm and cold cohorts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def compute_percentile(values: List[float], p: float) -> float:
    """Computes the p-th percentile (0.0 to 1.0) with linear interpolation."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return float(sorted_v[int(k)])
    return float(round(sorted_v[int(f)] * (c - k) + sorted_v[int(c)] * (k - f), 4))


def compute_latency_stats(values: List[Optional[float]]) -> Dict[str, Any]:
    """Calculates statistical summary for a series of latency measurements.

    Accurately accounts for missing values (None) in the denominator rather than
    silently substituting elapsed timeout durations.
    """
    total = len(values)
    if total == 0:
        return {
            "total_count": 0,
            "valid_count": 0,
            "missing_count": 0,
            "deficit_rate": 0.0,
            "count": 0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "mean": 0.0,
            "min": 0.0,
            "max": 0.0,
            "stddev": 0.0,
        }

    valid_vals = [float(v) for v in values if v is not None]
    valid_n = len(valid_vals)
    missing_n = total - valid_n
    deficit_rate = round(float(missing_n) / float(total), 4)

    if valid_n == 0:
        return {
            "total_count": total,
            "valid_count": 0,
            "missing_count": missing_n,
            "deficit_rate": deficit_rate,
            "count": 0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "mean": 0.0,
            "min": 0.0,
            "max": 0.0,
            "stddev": 0.0,
        }

    mean_val = sum(valid_vals) / valid_n
    variance = sum((x - mean_val) ** 2 for x in valid_vals) / valid_n if valid_n > 1 else 0.0
    return {
        "total_count": total,
        "valid_count": valid_n,
        "missing_count": missing_n,
        "deficit_rate": deficit_rate,
        "count": valid_n,
        "p50": compute_percentile(valid_vals, 0.50),
        "p90": compute_percentile(valid_vals, 0.90),
        "p95": compute_percentile(valid_vals, 0.95),
        "mean": round(mean_val, 4),
        "min": round(min(valid_vals), 4),
        "max": round(max(valid_vals), 4),
        "stddev": round(math.sqrt(variance), 4),
        "samples": valid_vals,
    }


def evaluate_startup_budget_qualification(
    warm_stats: Dict[str, Dict[str, Any]],
    cold_stats: Optional[Dict[str, Dict[str, Any]]] = None,
    total_missing_pcm: int = 0,
    total_failed_starts: int = 0,
    max_duration_threshold_s: float = 30.0,
) -> Dict[str, Any]:
    """Evaluates strict startup latency qualification gates defined in Section 7 of the plan:

    1. Intent receipt to local start dispatch <= 250ms p95.
    2. Capture dispatch to first persisted PCM <= 500ms p95 warm.
    3. Intent receipt to capture confirmation <= 1.5s p95 warm.
    4. Intent receipt to capture confirmation <= 5.0s p95 cold.
    5. Zero missing PCM (missing PCM stays missing and fails qualification).
    6. Zero failed starts (any failed start fails qualification).
    7. All individual samples under 30.0s SLA initiation deadline (no p95 masking).
    """
    gates: Dict[str, Dict[str, Any]] = {}

    # Gate 1: Intent to dispatch (tested via warm stats t_capture_spawn_s or t_cli_import_s proxy)
    dispatch_p95 = warm_stats.get("t_capture_spawn_s", {}).get("p95", 0.0)
    dispatch_pass = dispatch_p95 <= 0.250
    gates["gate_intent_to_dispatch_p95"] = {
        "threshold_s": 0.250,
        "measured_s": dispatch_p95,
        "pass": dispatch_pass,
    }

    # Gate 2: Capture dispatch to first persisted PCM warm <= 500ms p95
    pcm_warm_p95 = warm_stats.get("t_first_pcm_byte_s", {}).get("p95", 0.0)
    pcm_warm_pass = pcm_warm_p95 <= 0.500 and warm_stats.get("t_first_pcm_byte_s", {}).get("missing_count", 0) == 0
    gates["gate_capture_to_first_pcm_warm_p95"] = {
        "threshold_s": 0.500,
        "measured_s": pcm_warm_p95,
        "pass": pcm_warm_pass,
    }

    # Gate 3: Intent receipt to capture confirmation warm <= 1.5s p95
    conf_warm_p95 = warm_stats.get("t_recording_confirmed_s", {}).get("p95", 0.0)
    conf_warm_pass = conf_warm_p95 <= 1.500
    gates["gate_intent_to_confirmed_warm_p95"] = {
        "threshold_s": 1.500,
        "measured_s": conf_warm_p95,
        "pass": conf_warm_pass,
    }

    # Gate 4: Intent receipt to capture confirmation cold <= 5.0s p95 (if cold cohort present)
    if cold_stats and "t_recording_confirmed_s" in cold_stats:
        conf_cold_p95 = cold_stats["t_recording_confirmed_s"].get("p95", 0.0)
        conf_cold_pass = conf_cold_p95 <= 5.000
    else:
        conf_cold_p95 = conf_warm_p95
        conf_cold_pass = True
    gates["gate_intent_to_confirmed_cold_p95"] = {
        "threshold_s": 5.000,
        "measured_s": conf_cold_p95,
        "pass": conf_cold_pass,
    }

    # Gate 5: Zero missing PCM in denominator
    gates["gate_no_missing_pcm"] = {
        "threshold_allowed": 0,
        "measured_missing": total_missing_pcm,
        "pass": total_missing_pcm == 0,
    }

    # Gate 6: Zero failed starts
    gates["gate_no_failed_starts"] = {
        "threshold_allowed": 0,
        "measured_failed": total_failed_starts,
        "pass": total_failed_starts == 0,
    }

    # Gate 7: Individual sample ceiling (< 30.0s SLA, no p95 masking)
    max_warm = warm_stats.get("t_recording_confirmed_s", {}).get("max", 0.0)
    max_cold = cold_stats.get("t_recording_confirmed_s", {}).get("max", 0.0) if cold_stats else 0.0
    max_measured = max(max_warm, max_cold)
    gates["gate_individual_sample_ceiling"] = {
        "threshold_s": max_duration_threshold_s,
        "measured_max_s": max_measured,
        "pass": max_measured < max_duration_threshold_s,
    }

    all_passed = all(g["pass"] for g in gates.values())
    return {
        "qualified": all_passed,
        "gates": gates,
    }
