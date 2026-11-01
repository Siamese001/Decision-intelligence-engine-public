"""
dual_pipeline/telemetry_core/metrics.py

Metrics collection and aggregation engine for Decision Intelligence pipelines.
Provides thread-safe counters, histograms, and gauges with OpenTelemetry compatibility,
label support, and exportable snapshots for observability dashboards and test assertions.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.metrics")

_METRICS_LOCK = threading.Lock()
_COUNTERS: Dict[Tuple[str, str], float] = defaultdict(float)
_HISTOGRAMS: Dict[Tuple[str, str], List[float]] = defaultdict(list)
_GAUGES: Dict[Tuple[str, str], float] = {}


def _serialize_labels(labels: Optional[Dict[str, str]]) -> str:
    if not labels:
        return ""
    sorted_items = sorted((str(k), str(v)) for k, v in labels.items())
    return ",".join(f"{k}={v}" for k, v in sorted_items)


def increment_counter(
    name: str,
    value: float = 1.0,
    labels: Optional[Dict[str, str]] = None,
) -> None:
    """Increment a monotonic counter with optional labels."""
    label_str = _serialize_labels(labels)
    with _METRICS_LOCK:
        _COUNTERS[(name, label_str)] += float(value)


def record_histogram(
    name: str,
    value: float,
    labels: Optional[Dict[str, str]] = None,
) -> None:
    """Record a duration or size observation in a histogram."""
    label_str = _serialize_labels(labels)
    with _METRICS_LOCK:
        _HISTOGRAMS[(name, label_str)].append(float(value))


def set_gauge(
    name: str,
    value: float,
    labels: Optional[Dict[str, str]] = None,
) -> None:
    """Set the current value of a gauge."""
    label_str = _serialize_labels(labels)
    with _METRICS_LOCK:
        _GAUGES[(name, label_str)] = float(value)


def get_counter_value(name: str, labels: Optional[Dict[str, str]] = None) -> float:
    """Retrieve current total for a specific counter and label set."""
    label_str = _serialize_labels(labels)
    with _METRICS_LOCK:
        return _COUNTERS.get((name, label_str), 0.0)


def get_histogram_values(name: str, labels: Optional[Dict[str, str]] = None) -> List[float]:
    """Retrieve recorded samples for a specific histogram and label set."""
    label_str = _serialize_labels(labels)
    with _METRICS_LOCK:
        return list(_HISTOGRAMS.get((name, label_str), []))


def reset_metrics() -> None:
    """Clear all recorded metrics (primarily for test isolation)."""
    with _METRICS_LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()
        _GAUGES.clear()


def get_metrics_snapshot() -> Dict[str, Any]:
    """
    Returns an aggregated snapshot of all collected counters, histograms, and gauges.
    """
    with _METRICS_LOCK:
        counters_out = []
        for (name, label_str), val in _COUNTERS.items():
            counters_out.append({
                "name": name,
                "labels": label_str,
                "value": val,
            })

        histograms_out = []
        for (name, label_str), samples in _HISTOGRAMS.items():
            count = len(samples)
            total = sum(samples)
            avg = total / count if count > 0 else 0.0
            histograms_out.append({
                "name": name,
                "labels": label_str,
                "count": count,
                "sum": round(total, 4),
                "avg": round(avg, 4),
                "min": round(min(samples), 4) if samples else 0.0,
                "max": round(max(samples), 4) if samples else 0.0,
            })

        gauges_out = []
        for (name, label_str), val in _GAUGES.items():
            gauges_out.append({
                "name": name,
                "labels": label_str,
                "value": val,
            })

    return {
        "timestamp": time.time(),
        "counters": counters_out,
        "histograms": histograms_out,
        "gauges": gauges_out,
    }


# High-level pipeline metric helper functions
def record_ingestion_metric(status: str, project: str) -> None:
    increment_counter("ingestion.total", 1.0, {"status": status, "project": project})


def record_error_metric(stage: str, error_type: str) -> None:
    increment_counter("errors.total", 1.0, {"stage": stage, "error_type": error_type})


def record_stage_duration(stage: str, duration_seconds: float, status: str = "PASS") -> None:
    record_histogram("stage.duration_seconds", duration_seconds, {"stage": stage, "status": status})


def record_llm_call_metrics(
    model: str,
    duration_seconds: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> None:
    increment_counter("llm.calls.total", 1.0, {"model": model})
    pt = float(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else 0.0
    ct = float(completion_tokens) if isinstance(completion_tokens, (int, float)) else 0.0
    rt = float(reasoning_tokens) if isinstance(reasoning_tokens, (int, float)) else 0.0
    if pt > 0:
        increment_counter("tokens.prompt.total", pt, {"model": model})
    if ct > 0:
        increment_counter("tokens.completion.total", ct, {"model": model})
    if rt > 0:
        increment_counter("tokens.reasoning.total", rt, {"model": model})
    dur = float(duration_seconds) if isinstance(duration_seconds, (int, float)) else 0.0
    record_histogram("llm.duration_seconds", dur, {"model": model})


def format_prometheus_metrics() -> str:
    """Format all current metrics into standard Prometheus text exposition format."""
    lines: List[str] = [
        "# HELP nee_up Process operational indicator",
        "# TYPE nee_up gauge",
        "nee_up 1",
        "",
    ]

    with _METRICS_LOCK:
        counter_types_emitted: set[str] = set()
        for (name, label_str), val in _COUNTERS.items():
            prom_name = "nee_" + name.replace(".", "_").replace("-", "_")
            if prom_name not in counter_types_emitted:
                lines.append(f"# TYPE {prom_name} counter")
                counter_types_emitted.add(prom_name)
            labels_formatted = ""
            if label_str:
                parts = []
                for item in label_str.split(","):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        parts.append(f'{k}="{v}"')
                if parts:
                    labels_formatted = "{" + ",".join(parts) + "}"
            lines.append(f"{prom_name}{labels_formatted} {val}")
        if _COUNTERS:
            lines.append("")

        gauge_types_emitted: set[str] = set()
        for (name, label_str), val in _GAUGES.items():
            prom_name = "nee_" + name.replace(".", "_").replace("-", "_")
            if prom_name not in gauge_types_emitted:
                lines.append(f"# TYPE {prom_name} gauge")
                gauge_types_emitted.add(prom_name)
            labels_formatted = ""
            if label_str:
                parts = []
                for item in label_str.split(","):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        parts.append(f'{k}="{v}"')
                if parts:
                    labels_formatted = "{" + ",".join(parts) + "}"
            lines.append(f"{prom_name}{labels_formatted} {val}")
        if _GAUGES:
            lines.append("")

        hist_types_emitted: set[str] = set()
        for (name, label_str), samples in _HISTOGRAMS.items():
            prom_name = "nee_" + name.replace(".", "_").replace("-", "_")
            if prom_name not in hist_types_emitted:
                lines.append(f"# TYPE {prom_name} histogram")
                hist_types_emitted.add(prom_name)
            labels_base = {}
            if label_str:
                for item in label_str.split(","):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        labels_base[k] = v
            count = len(samples)
            total = sum(samples)
            count_lbls = ",".join(f'{k}="{v}"' for k, v in sorted(labels_base.items()))
            count_str = f"{{{count_lbls}}}" if count_lbls else ""
            lines.append(f"{prom_name}_count{count_str} {count}")
            lines.append(f"{prom_name}_sum{count_str} {round(total, 4)}")
        if _HISTOGRAMS:
            lines.append("")

    return "\n".join(lines) + "\n"

