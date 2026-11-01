"""Historical trend analysis and metric drift detection across generations."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _compute_mean_and_std(values: list[float]) -> tuple[float, float]:
    """Compute arithmetic mean and sample standard deviation."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(variance)


def _count_consecutive_declines(values: list[float]) -> int:
    """Count how many consecutive times the series has strictly decreased up to the end."""
    if len(values) < 2:
        return 0
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] < values[i - 1]:
            count += 1
        else:
            break
    return count


class MetricTrendAnalyzer:
    """Track rolling metric history across generations and detect anomalous degradation drift."""

    def __init__(
        self,
        history: list[dict[str, Any]] | None = None,
        *,
        min_history_for_stats: int = 3,
        window_size: int = 20,
    ) -> None:
        self.history: list[dict[str, Any]] = list(history) if history is not None else []
        self.min_history_for_stats = min_history_for_stats
        self.window_size = window_size

    def record_generation(
        self,
        generation_id: str,
        metrics: dict[str, float | int],
        timestamp: str | None = None,
    ) -> None:
        """Record metrics for an activated generation."""
        entry: dict[str, Any] = {
            "generation_id": generation_id,
            "metrics": {k: float(v) for k, v in metrics.items()},
        }
        if timestamp:
            entry["timestamp"] = timestamp
        self.history.append(entry)

    def get_metric_series(self, metric_name: str) -> list[float]:
        """Extract historical float series for a given metric key."""
        series: list[float] = []
        for item in self.history:
            m = item.get("metrics", {})
            if metric_name in m:
                series.append(float(m[metric_name]))
        return series

    def analyze_metric_drift(self, metric_name: str) -> dict[str, Any]:
        """Analyze a metric for 2-sigma drift or consecutive decline degradation."""
        series = self.get_metric_series(metric_name)
        if not series:
            return {
                "metric": metric_name,
                "status": "INSUFFICIENT_DATA",
                "details": f"No data recorded for metric {metric_name}",
            }

        current = series[-1]
        prior = series[-self.window_size : -1] if len(series) > 1 else []

        mean, std = _compute_mean_and_std(prior) if prior else (current, 0.0)
        has_stats = len(prior) >= self.min_history_for_stats and std > 1e-9

        z_score = (current - mean) / std if has_stats else 0.0
        sigma_drift = has_stats and current < (mean - 2.0 * std)

        decline_count = _count_consecutive_declines(series)
        consecutive_declines_detected = decline_count >= 3

        is_alert = sigma_drift or consecutive_declines_detected
        status = "ALERT" if is_alert else "HEALTHY"

        issues: list[str] = []
        if sigma_drift:
            issues.append(
                f"Value {current:.4f} is > 2σ below rolling mean ({mean:.4f} - 2*{std:.4f} = {mean - 2 * std:.4f})"
            )
        if consecutive_declines_detected:
            issues.append(f"{decline_count} consecutive declines observed")

        details = "; ".join(issues) if issues else "Metric within normal operational range"

        return {
            "metric": metric_name,
            "status": status,
            "current_value": round(current, 4),
            "rolling_mean": round(mean, 4),
            "rolling_std": round(std, 4),
            "z_score": round(z_score, 4),
            "sigma_drift_detected": sigma_drift,
            "consecutive_declines": decline_count,
            "consecutive_declines_detected": consecutive_declines_detected,
            "history_length": len(series),
            "details": details,
        }

    def analyze_all_metrics(self) -> dict[str, Any]:
        """Analyze all observed metrics across history."""
        metric_names: set[str] = set()
        for item in self.history:
            metric_names.update(item.get("metrics", {}).keys())

        results: dict[str, Any] = {}
        alerts: list[str] = []
        for name in sorted(metric_names):
            res = self.analyze_metric_drift(name)
            results[name] = res
            if res.get("status") == "ALERT":
                alerts.append(f"{name}: {res.get('details')}")

        overall_status = "ALERT" if alerts else "HEALTHY"
        return {
            "status": overall_status,
            "metrics": results,
            "alerts": alerts,
            "total_metrics_evaluated": len(results),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dictionary."""
        return {
            "history": self.history,
            "min_history_for_stats": self.min_history_for_stats,
            "window_size": self.window_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricTrendAnalyzer:
        """Instantiate analyzer from serialized dictionary."""
        return cls(
            history=data.get("history", []),
            min_history_for_stats=data.get("min_history_for_stats", 3),
            window_size=data.get("window_size", 20),
        )

    def save(self, path: Path | str) -> None:
        """Save history to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> MetricTrendAnalyzer:
        """Load history from JSON file."""
        p = Path(path)
        if not p.is_file():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_dict(data)
