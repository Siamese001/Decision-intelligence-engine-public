"""Canonical pipeline release policy facade."""
from __future__ import annotations

from dual_pipeline.release_policy import (
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    RETRIEVAL_RELEASE_11_GATES,
    _check_out3_threshold,
    evaluate_retrieval_11_gates,
    validate_micro_judge_scorecard,
    validate_scorecard_provenance,
)

__all__ = [
    "OUTPUT1_PRODUCTION_PASS_THRESHOLD",
    "OUTPUT2_PRODUCTION_PASS_THRESHOLD",
    "OUTPUT3_PRODUCTION_PASS_THRESHOLD",
    "RETRIEVAL_RELEASE_11_GATES",
    "_check_out3_threshold",
    "evaluate_retrieval_11_gates",
    "validate_micro_judge_scorecard",
    "validate_scorecard_provenance",
]
