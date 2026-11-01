"""Strategic Intelligence Evaluation Models ($1.2M Executive Exoskeleton Benchmark).

Governed by:
- governance/decision_intelligence/EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md
- governance/decision_intelligence/EXECUTIVE_GRAPH_AUDIT_REPORT_1_2M.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class DimensionScore:
    """Evaluation score and diagnostic evidence for a single Strategic Intelligence Dimension."""
    dim_id: str
    name: str
    score: float  # 0.0 to 100.0
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dim_id": self.dim_id,
            "name": self.name,
            "score": round(self.score, 2),
            "passed": self.passed,
            "details": self.details,
        }


@dataclass
class MCDACriteriaWeights:
    """Multi-Criteria Decision Analysis (MCDA) executive weight distribution."""
    tco_finops: float = 0.25
    time_to_value: float = 0.20
    architectural_reversibility: float = 0.20
    security_compliance: float = 0.20
    org_friction: float = 0.15

    def validate(self) -> bool:
        total = (
            self.tco_finops
            + self.time_to_value
            + self.architectural_reversibility
            + self.security_compliance
            + self.org_friction
        )
        return abs(total - 1.0) < 1e-4


@dataclass
class DecisionOptionEvaluation:
    """MCDA scored strategic option with financialized cost-of-delay quantification."""
    option_id: str
    name: str
    composite_score: float
    cost_of_delay_monthly_usd: float
    rank: int = 1


@dataclass
class ExecutiveExoskeletonScorecard:
    """Composite scorecard across all 10 Strategic Intelligence Dimensions."""
    dimension_scores: Dict[str, DimensionScore] = field(default_factory=dict)
    overall_score: float = 0.0
    maturity_level: float = 1.0
    passed: bool = False
    verdict: str = "Level 1: Unstructured Secretary"

    def compute_summary(self, passing_threshold: float = 75.0) -> None:
        """Compute average score, maturity level, and qualitative verdict."""
        if not self.dimension_scores:
            self.overall_score = 0.0
            self.maturity_level = 1.0
            self.passed = False
            self.verdict = "Level 1: Unstructured Secretary"
            return

        scores = [ds.score for ds in self.dimension_scores.values()]
        self.overall_score = round(sum(scores) / len(scores), 2)
        # Scale 0-100 to 1.0-5.0 maturity scale
        self.maturity_level = round(1.0 + (self.overall_score / 100.0) * 4.0, 2)

        # Fail-closed: all dimensions must independently pass
        all_dims_passed = all(ds.passed for ds in self.dimension_scores.values())
        self.passed = all_dims_passed and (self.overall_score >= passing_threshold)

        if self.maturity_level >= 4.5:
            self.verdict = "Level 5: Executive Cognitive Exoskeleton ($1.2M Benchmark)"
        elif self.maturity_level >= 3.8:
            self.verdict = "Level 4: Autonomous Strategic Partner"
        elif self.maturity_level >= 3.0:
            self.verdict = "Level 3: Contextual Strategic Advisor"
        elif self.maturity_level >= 2.0:
            self.verdict = "Level 2: Structured Meeting Logger"
        else:
            self.verdict = "Level 1: Unstructured Secretary"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "maturity_level": self.maturity_level,
            "passed": self.passed,
            "verdict": self.verdict,
            "dimensions": {k: v.to_dict() for k, v in self.dimension_scores.items()},
        }
