"""L1 Semantic Sufficiency Evaluator for Sealed L2 Observations.

Cognitive evaluator that analyzes:
  original objective + original plan + expected outcome + sealed observation
to determine whether the subgoal was accomplished or requires L1 re-planning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from config.credentials import CredentialManager
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from storage.credentials import resolve_openai_credentials


@dataclass(frozen=True)
class SemanticSufficiencyResult:
    """Detailed evaluation of observation sufficiency against L1 objective."""
    is_sufficient: bool
    subgoal_coverage_score: float
    unanswered_subquestions: tuple[str, ...] = field(default_factory=tuple)
    missing_aspects: tuple[str, ...] = field(default_factory=tuple)
    replan_directive: Optional[dict[str, Any]] = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_sufficient": self.is_sufficient,
            "subgoal_coverage_score": round(self.subgoal_coverage_score, 3),
            "unanswered_subquestions": list(self.unanswered_subquestions),
            "missing_aspects": list(self.missing_aspects),
            "replan_directive": self.replan_directive,
            "rationale": self.rationale,
        }


_SYNTHESIS_KEYS = (
    "rationale",
    "current_state",
    "patterns",
    "pain_points",
    "opportunities",
    "hypotheses",
    "next_best_actions",
    "proposed_assertions",
    "assumptions",
    "what_would_change_the_conclusion",
    "counterevidence",
    "source_evidence",
    "summary",
    "guidance",
    "architecture_overview",
)

_STOPWORDS = frozenset({
    "which", "what", "where", "when", "exist", "match", "request",
    "support", "supports", "document", "them", "these", "those",
    "does", "have", "with", "from", "into", "about",
})


def _extract_text_tokens(val: Any) -> list[str]:
    if isinstance(val, str):
        return [val]
    if isinstance(val, (list, tuple)):
        parts: list[str] = []
        for item in val:
            parts.extend(_extract_text_tokens(item))
        return parts
    if isinstance(val, Mapping):
        return [str(v) for v in val.values() if isinstance(v, (str, int, float))]
    return []


def _extract_synthesis_text(answer: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for k in _SYNTHESIS_KEYS:
        v = answer.get(k)
        if v is not None:
            parts.extend(_extract_text_tokens(v))
    return " ".join(parts).lower()


def _token_matches(tok: str, text: str) -> bool:
    if tok in text:
        return True
    stem = (
        tok[:-3] if tok.endswith("ies")
        else (tok[:-2] if tok.endswith("ed")
        else (tok[:-1] if tok.endswith("s") else tok))
    )
    return len(stem) >= 4 and stem in text


def _is_subquestion_covered(sq: str, text: str) -> bool:
    raw_tokens = set(re.findall(r"\b[a-z]{4,}\b", sq.lower()))
    meaningful = raw_tokens - _STOPWORDS
    if not meaningful:
        return True
    matches = sum(1 for tok in meaningful if _token_matches(tok, text))
    return (matches / len(meaningful)) >= 0.40


def _analyze_subquestion_coverage(
    subquestions: Sequence[str], text: str
) -> tuple[float, list[str]]:
    unanswered: list[str] = []
    covered = 0
    for sq in subquestions:
        if _is_subquestion_covered(sq, text):
            covered += 1
        else:
            unanswered.append(sq)
    score = covered / max(len(subquestions), 1)
    return score, unanswered


def _check_discrete_findings(
    answer: Mapping[str, Any], query_type: Optional[str]
) -> list[str]:
    keys = ("opportunities", "gaps", "risks", "items", "entities", "claims", "current_state")
    total = sum(
        len(answer[k])
        for k in keys
        if isinstance(answer.get(k), (list, tuple))
    )
    if total == 0 and query_type in (
        "opportunity_nba",
        "gap_risk_inconsistency",
        "relationship_explanation",
    ):
        return ["empty_discrete_findings"]
    return []


def _adjust_coverage_for_governed_schema(
    answer: Mapping[str, Any],
    coverage_score: float,
    unanswered: list[str],
    subquestions: list[str],
    missing_aspects: list[str],
) -> tuple[float, list[str]]:
    rationale_text = str(answer.get("rationale") or "").strip().lower()
    is_partial = any(w in rationale_text for w in ("incomplete", "partial", "missing key details", "omitting"))
    if is_partial:
        return min(coverage_score, 0.50), (unanswered or subquestions[:1])
    has_findings = any(
        answer.get(k) for k in ("current_state", "pain_points", "opportunities", "proposed_assertions", "source_evidence")
    )
    if "schema_version" in answer and rationale_text and has_findings and not missing_aspects:
        return max(coverage_score, 0.85), []
    return coverage_score, unanswered


class L1SemanticSufficiencyEvaluator:
    """Evaluates sealed L2 observations against the L1 plan objective."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock_mode: bool = False,
        query_expansion_provider: Optional[Any] = None,
    ) -> None:
        creds = resolve_openai_credentials(allow_mock=mock_mode)
        default_model, default_effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.model_id = (
            model_id
            or CredentialManager.get_credential("OPENAI_MODEL")
            or creds.model
            or default_model
        )
        self.reasoning_effort = (
            reasoning_effort
            or CredentialManager.get_credential("OPENAI_REASONING_EFFORT")
            or creds.reasoning_effort
            or default_effort
            or "high"
        )
        self.mock_mode = mock_mode or not bool(creds.api_key)
        self.query_expansion_provider = query_expansion_provider

    def evaluate_sufficiency(
        self,
        *,
        question: str,
        plan: Mapping[str, Any],
        answer: Optional[Mapping[str, Any]],
        sealed_l2: Mapping[str, Any],
        evidence_packet: Optional[Mapping[str, Any]] = None,
    ) -> SemanticSufficiencyResult:
        """Determines whether the sealed observation fulfills the plan objective."""
        if answer is None:
            return SemanticSufficiencyResult(
                is_sufficient=False,
                subgoal_coverage_score=0.0,
                missing_aspects=("answer_payload",),
                replan_directive={
                    "directive_type": "PRIMARY_EXECUTION_REQUIRED",
                    "focus": "Execute tool to generate initial observation",
                },
                rationale="Sealed observation contains null answer.",
            )

        subquestions = list(plan.get("subquestions") or [question])
        combined_text = _extract_synthesis_text(answer)
        coverage_score, unanswered = _analyze_subquestion_coverage(subquestions, combined_text)
        missing_aspects = _check_discrete_findings(answer, plan.get("query_type"))
        coverage_score, unanswered = _adjust_coverage_for_governed_schema(
            answer, coverage_score, unanswered, subquestions, missing_aspects
        )

        is_sufficient = coverage_score >= 0.75 and len(missing_aspects) == 0
        if is_sufficient:
            return SemanticSufficiencyResult(
                is_sufficient=True,
                subgoal_coverage_score=coverage_score,
                rationale=f"All subgoals satisfactorily addressed (coverage={coverage_score:.2f}).",
            )

        suggested_expansions: list[str] = []
        if self.query_expansion_provider is not None and unanswered:
            for sq in unanswered[:3]:
                expanded = self.query_expansion_provider.expand_query(sq, count=2)
                suggested_expansions.extend(expanded)
        if not suggested_expansions:
            suggested_expansions = [f"Evidence for: {sq}" for sq in unanswered[:3]]

        replan_directive = {
            "directive_type": "FOCUSED_SUBQUESTION_REPLAN", "coverage_score": round(coverage_score, 3),
            "unanswered_subquestions": unanswered, "missing_aspects": missing_aspects,
            "suggested_query_expansion": suggested_expansions,
            "recommended_focus": f"Address specific subquestions: {unanswered[0]}" if unanswered else "Populate discrete findings in answer",
        }
        return SemanticSufficiencyResult(
            is_sufficient=False, subgoal_coverage_score=coverage_score,
            unanswered_subquestions=tuple(unanswered), missing_aspects=tuple(missing_aspects),
            replan_directive=replan_directive,
            rationale=f"Subgoal coverage insufficient (score={coverage_score:.2f}). Unanswered: {unanswered[:2]}.",
        )
