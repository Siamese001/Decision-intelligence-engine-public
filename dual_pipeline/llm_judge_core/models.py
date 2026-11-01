"""Pydantic models and dataclasses for LLM Judge scoring."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from dual_pipeline.parameters import get_svp_political_materiality_threshold


class JudgeAgenticLeverage(BaseModel):
    is_actionable_wedge: bool = Field(..., description="Does this insight provide the user with a distinct, socially acceptable excuse to message or meet a high-level stakeholder?")
    political_materiality: int = Field(..., description="Scale 1-10. 10 = An executive is blind to a critical risk that the user can swoop in and solve.")
    networking_target_identified: bool = Field(..., description="Did the extractor identify a specific human target for the user to connect with?")
    pass_gate: bool = Field(
        ...,
        description=(
            f"Final Verdict. MUST be FALSE if political_materiality < "
            f"{get_svp_political_materiality_threshold(is_executive=False)} (standard) / "
            f"{get_svp_political_materiality_threshold(is_executive=True)} (executive) "
            f"OR networking_target_identified is False."
        ),
    )
    rejection_reason: Optional[str] = Field(None, description="If pass_gate is FALSE, concisely state why this node fails to provide career leverage.")


@dataclass
class MicroJudgeScorecard:
    composite_score: int
    verdict: str  # "PASS" | "PASS_ADVISORY" | "FAIL"
    dimension_scores: Dict[str, int]
    forensic_rationale: str
    actionable_remediation_diff: str
    grounding_citations: List[str] = field(default_factory=list)
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class BatchedEvaluationResult:
    batch_id: str
    item_count: int
    evaluated_items: List[Dict[str, Any]]
    deduplicated_count: int = 0
    estimated_tokens_saved: int = 0
    duration_ms: float = 0.0
    fallback_invoked: bool = False
    position_swap_verified: bool = False
    position_bias_detected: bool = False
    swap_consistency_rate: float = 1.0
