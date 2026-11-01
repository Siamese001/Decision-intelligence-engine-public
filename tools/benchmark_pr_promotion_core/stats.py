"""Distribution statistics and percentile computations for promotion latency."""

from __future__ import annotations

import math
from typing import Dict, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Calculates percentile p in [0, 100] using linear interpolation."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(float(sorted_vals[int(k)]), 4)
    d0 = float(sorted_vals[int(f)]) * (c - k)
    d1 = float(sorted_vals[int(c)]) * (k - f)
    return round(d0 + d1, 4)


def compute_stats(values: Sequence[float]) -> Dict[str, float]:
    """Computes basic distribution statistics."""
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
        }
    vals = [float(v) for v in values]
    mean_val = sum(vals) / len(vals)
    return {
        "count": len(vals),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "mean": round(mean_val, 4),
        "p50": percentile(vals, 50.0),
        "p90": percentile(vals, 90.0),
        "p95": percentile(vals, 95.0),
    }
