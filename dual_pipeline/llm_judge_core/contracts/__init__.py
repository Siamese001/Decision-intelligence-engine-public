"""Contracts package for LLM judge core."""
from __future__ import annotations

from .judge_criterion_contract import (
    CriterionApplicability,
    CriterionDeduction,
    JudgeCriterion,
    ScorecardValidationResult,
    resolve_applicable_criteria,
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
    validate_score_value,
)

__all__ = [
    "CriterionApplicability",
    "CriterionDeduction",
    "JudgeCriterion",
    "ScorecardValidationResult",
    "resolve_applicable_criteria",
    "validate_output1_scorecard",
    "validate_output2_scorecard",
    "validate_output3_scorecard",
    "validate_score_value",
]
