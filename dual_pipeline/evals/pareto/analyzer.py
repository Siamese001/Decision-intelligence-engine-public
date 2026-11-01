"""
Multi-Dimensional Pareto Frontier and Token Economics Analyzer.

Evaluates trade-offs across:
- Quality Score (higher is better)
- Latency (ms, lower is better)
- Token Cost / Microcredits (lower is better)
- Reasoning Effort Tier (none, low, medium, high)

Identifies non-dominated Pareto-optimal configurations and flags regressions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ParetoPoint:
    """A single configuration's multi-objective performance coordinates."""

    config_id: str
    quality_score: float  # [0.0, 100.0]
    latency_ms: float     # milliseconds
    cost_microcredits: float  # deterministic cost unit
    reasoning_effort: str  # "none", "low", "medium", "high"
    is_pareto_optimal: bool = False
    dominated_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegressionVerdict:
    """Assessment of whether a candidate configuration regressed against a baseline."""

    candidate_id: str
    baseline_id: str
    is_regression: bool
    quality_delta: float
    latency_delta_pct: float
    cost_delta_pct: float
    reasons: List[str] = field(default_factory=list)


class ParetoFrontierAnalyzer:
    """Computes Pareto dominance frontiers and detects performance regressions."""

    def __init__(
        self,
        max_quality_drop_pct: float = 5.0,
        max_latency_spike_pct: float = 25.0,
    ):
        self.max_quality_drop_pct = max_quality_drop_pct
        self.max_latency_spike_pct = max_latency_spike_pct

    def compute_frontier(self, points: List[ParetoPoint]) -> List[ParetoPoint]:
        """
        Compute non-dominated Pareto frontier across (Quality, Latency, Cost).
        
        Point A dominates Point B iff:
          - quality(A) >= quality(B)
          - latency(A) <= latency(B)
          - cost(A) <= cost(B)
          - at least one inequality is strict.
        """
        if not points:
            return []

        # Reset dominance state
        for p in points:
            p.is_pareto_optimal = True
            p.dominated_by = []

        n = len(points)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                p_i = points[i]
                p_j = points[j]

                # Check if p_j dominates p_i
                j_at_least_as_good = (
                    (p_j.quality_score >= p_i.quality_score)
                    and (p_j.latency_ms <= p_i.latency_ms)
                    and (p_j.cost_microcredits <= p_i.cost_microcredits)
                )
                j_strictly_better = (
                    (p_j.quality_score > p_i.quality_score)
                    or (p_j.latency_ms < p_i.latency_ms)
                    or (p_j.cost_microcredits < p_i.cost_microcredits)
                )

                if j_at_least_as_good and j_strictly_better:
                    p_i.is_pareto_optimal = False
                    p_i.dominated_by.append(p_j.config_id)

        return points

    def evaluate_regression(
        self, candidate: ParetoPoint, baseline: ParetoPoint
    ) -> RegressionVerdict:
        """Evaluate if candidate exhibits a statistically unacceptable regression against baseline."""
        quality_delta = round(candidate.quality_score - baseline.quality_score, 2)

        base_lat = max(1.0, baseline.latency_ms)
        latency_delta_pct = round(
            ((candidate.latency_ms - baseline.latency_ms) / base_lat) * 100.0, 2
        )

        base_cost = max(1.0, baseline.cost_microcredits)
        cost_delta_pct = round(
            ((candidate.cost_microcredits - baseline.cost_microcredits) / base_cost)
            * 100.0,
            2,
        )

        reasons = []
        is_regression = False

        # Quality drop check
        if quality_delta < -self.max_quality_drop_pct:
            is_regression = True
            reasons.append(
                f"Quality dropped by {-quality_delta:.1f} pts (> {self.max_quality_drop_pct}%)"
            )

        # Latency spike check
        if latency_delta_pct > self.max_latency_spike_pct:
            is_regression = True
            reasons.append(
                f"Latency increased by {latency_delta_pct:.1f}% (> {self.max_latency_spike_pct}%)"
            )

        return RegressionVerdict(
            candidate_id=candidate.config_id,
            baseline_id=baseline.config_id,
            is_regression=is_regression,
            quality_delta=quality_delta,
            latency_delta_pct=latency_delta_pct,
            cost_delta_pct=cost_delta_pct,
            reasons=reasons,
        )
