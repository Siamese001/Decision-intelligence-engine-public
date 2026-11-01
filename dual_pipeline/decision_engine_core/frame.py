"""
dual_pipeline/decision_engine_core/frame.py

Pre-retrieval decision frame construction and schema validation.
"""

from __future__ import annotations

from typing import Any, Sequence

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import digest, stable_id
from dual_pipeline.decision_models import validate_decision_frame

from .constants import (
    DEFAULT_EVALUATIVE_OPTIONS,
    extract_criteria,
    is_evaluative_counterfactual_question,
    text_field,
    unique_text_list,
)
from .quantitative_math import parse_temporal_horizon


def _resolve_option_values(normalized_question: str, options: Sequence[str] | None) -> list[str]:
    values = unique_text_list(options, field="option")
    if not values and is_evaluative_counterfactual_question(normalized_question):
        return list(DEFAULT_EVALUATIVE_OPTIONS)
    return values


def _resolve_effective_horizon(time_horizon: str | None, normalized_question: str) -> tuple[str, dict[str, Any]]:
    raw_horizon = (time_horizon or "").strip()
    parsed = parse_temporal_horizon(raw_horizon or normalized_question)
    if raw_horizon:
        return raw_horizon, parsed
    if parsed.get("quarter"):
        return str(parsed["quarter"]), parsed
    if parsed.get("horizon_days") is not None:
        return f"{parsed['horizon_days']} days", parsed
    return "unspecified", parsed


def build_decision_frame(
    *,
    question: str,
    options: Sequence[str] | None = None,
    criteria: Sequence[str] | None = None,
    constraints: Sequence[str] | None = None,
    assumptions: Sequence[str] | None = None,
    time_horizon: str | None = None,
) -> dict[str, Any]:
    """Build the pre-retrieval decision contract without invented criteria."""
    normalized_question = text_field(question, field="question")
    option_values = _resolve_option_values(normalized_question, options)
    criterion_values = unique_text_list(criteria, field="criterion") or extract_criteria(normalized_question)
    constraint_values = unique_text_list(constraints, field="constraint")
    assumption_values = unique_text_list(assumptions, field="assumption")
    option_rows = [
        {"option_id": stable_id("decision-option", normalized_question, value, length=24), "label": value, "source": "caller", "rationale": None, "evidence_ids": []}
        for value in option_values
    ]
    criterion_rows = [
        {"criterion_id": stable_id("decision-criterion", normalized_question, value, length=24), "label": value, "weight": None, "source": "caller_or_explicit_question_clause"}
        for value in criterion_values
    ]
    missing_fields = [] if criterion_rows else ["criteria"]
    effective_horizon, parsed_horizon = _resolve_effective_horizon(time_horizon, normalized_question)
    frame_fields: dict[str, Any] = {
        "decision_question": normalized_question, "desired_outcome": normalized_question,
        "time_horizon": effective_horizon, "options": option_rows, "criteria": criterion_rows,
        "constraints": constraint_values, "assumptions": assumption_values,
        "missing_fields": missing_fields, "frame_state": "CLARIFICATION_REQUIRED" if missing_fields else "READY",
        "frame_digest": "",
    }
    frame_fields["frame_digest"] = digest({key: value for key, value in frame_fields.items() if key != "frame_digest"})
    frame = create_artifact(
        "decision-frame",
        stable_id("decision-frame", normalized_question, option_values, criterion_values, length=26),
        extensions={"com.partner_advisory.nee.temporal-horizon": parsed_horizon},
        **frame_fields,
    )
    return validate_decision_frame(frame)
