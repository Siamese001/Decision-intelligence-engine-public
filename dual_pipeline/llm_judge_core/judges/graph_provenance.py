"""Preserve original graph scorecard fields before rubric arithmetic normalizes them."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from dual_pipeline.llm_judge_core.judge_clarification import assign_clarified_graph_verdict


def assign_with_raw_scorecard(
    parsed: Dict[str, Any], dimension_keys: Sequence[str], dimension_floors: Mapping[str, int],
) -> Dict[str, Any]:
    """Capture provider values, replacing any provider-supplied provenance field."""
    keys = (*dimension_keys, "composite_score", "verdict")
    parsed["raw_judge_scorecard"] = {key: deepcopy(parsed[key]) for key in keys if key in parsed}
    return assign_clarified_graph_verdict(parsed, dimension_keys, dimension_floors)


class GraphProvenanceJudge:
    """Micro-judge ensuring graph scorecards retain raw provider provenance before rubric arithmetic."""

    def __init__(self, priors_path: Optional[Any] = None) -> None:
        self.priors_path = priors_path

    def get_borderline_band(self, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
        )
        p_path = priors_path if priors_path is not None else self.priors_path
        return get_calibrated_borderline_band("graph_provenance", output_num=3, priors_path=p_path)

    def evaluate_provenance(
        self,
        parsed: Dict[str, Any],
        dimension_keys: Sequence[str],
        dimension_floors: Mapping[str, int],
    ) -> Dict[str, Any]:
        return assign_with_raw_scorecard(parsed, dimension_keys, dimension_floors)


__all__ = [
    "assign_with_raw_scorecard",
    "GraphProvenanceJudge",
]
