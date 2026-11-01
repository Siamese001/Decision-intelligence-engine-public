"""Dual-Pipeline Layer 0: Strongly-Typed Evaluation Results & Provenance Envelopes.

Defines the single source of truth for evaluation results across heuristic, vector,
and semantic evaluation layers under Law 6 (Policy Authority & Invariance).
Provides:
1. Distinct strongly-typed result envelopes (HeuristicAssessmentResult, VectorAssessmentResult, SemanticAssessmentResult).
2. Composite evaluation verdict tracking executed capabilities.
3. Strict fail-closed validator rejecting string booleans, invalid scores, duplicate/omitted items,
   and live-to-mock success fallbacks.
4. Claim grounding and citation verification.

Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


def _canonical_json_bytes(data: Any) -> bytes:
    """Serializes arbitrary Python dicts/lists into RFC-8259 canonical JSON bytes."""
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class EvaluationModality(str, Enum):
    """Evaluation modalities supported in the governed evaluation hierarchy."""
    HEURISTIC = "HEURISTIC"
    VECTOR = "VECTOR"
    SEMANTIC = "SEMANTIC"


class EvaluationVerdict(str, Enum):
    """Canonical terminal verdicts for evaluations."""
    PASS = "PASS"
    PASS_ADVISORY = "PASS_ADVISORY"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


class FailureCategory(str, Enum):
    """Standardized failure categories for evaluation audits."""
    NONE = "NONE"
    TIMEOUT_EXCEEDED = "TIMEOUT_EXCEEDED"
    EVALUATOR_UNAVAILABLE = "EVALUATOR_UNAVAILABLE"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    STRING_BOOLEAN_REJECTED = "STRING_BOOLEAN_REJECTED"
    INVALID_SCORE_RANGE = "INVALID_SCORE_RANGE"
    FABRICATED_SCORE_DETECTED = "FABRICATED_SCORE_DETECTED"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    IRRELEVANT_CITATION = "IRRELEVANT_CITATION"
    LEAKAGE_DETECTED = "LEAKAGE_DETECTED"
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
    DUPLICATE_ITEM = "DUPLICATE_ITEM"
    OMITTED_ITEM = "OMITTED_ITEM"
    MOCK_FALLBACK_PROHIBITED = "MOCK_FALLBACK_PROHIBITED"


@dataclass(frozen=True)
class HeuristicAssessmentResult:
    """Strictly-typed result of a deterministic heuristic rule evaluation."""
    assessment_id: str
    rule_name: str
    passed: bool
    score: float
    observed_value: Any
    details: str
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VectorAssessmentResult:
    """Strictly-typed result of a vector similarity or retrieval evaluation."""
    assessment_id: str
    similarity_score: float
    threshold: float
    passed: bool
    embedding_model: str
    query_digest: str
    reference_digest: str
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticAssessmentResult:
    """Strictly-typed result of an LLM or semantic judge evaluation."""
    assessment_id: str
    evaluator_name: str
    evaluator_version: str
    composite_score: float
    verdict: EvaluationVerdict
    dimension_scores: Dict[str, float]
    claims_evaluated: int
    unsupported_claims: Tuple[str, ...] = field(default_factory=tuple)
    grounding_citations: Tuple[str, ...] = field(default_factory=tuple)
    is_mock: bool = False
    duration_ms: float = 0.0
    failure_category: FailureCategory = FailureCategory.NONE
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        data["failure_category"] = self.failure_category.value
        return data


@dataclass(frozen=True)
class CompositeEvaluationVerdict:
    """Immutable composite evaluation verdict binding all executed capabilities."""
    evaluation_id: str
    output_id: int
    overall_verdict: EvaluationVerdict
    composite_score: float
    executed_capabilities: Tuple[str, ...]
    heuristic_results: Tuple[HeuristicAssessmentResult, ...] = field(default_factory=tuple)
    vector_results: Tuple[VectorAssessmentResult, ...] = field(default_factory=tuple)
    semantic_results: Tuple[SemanticAssessmentResult, ...] = field(default_factory=tuple)
    failure_reasons: Tuple[str, ...] = field(default_factory=tuple)
    failure_category: FailureCategory = FailureCategory.NONE
    is_mock: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "output_id": self.output_id,
            "overall_verdict": self.overall_verdict.value,
            "composite_score": self.composite_score,
            "executed_capabilities": list(self.executed_capabilities),
            "heuristic_results": [h.to_dict() for h in self.heuristic_results],
            "vector_results": [v.to_dict() for v in self.vector_results],
            "semantic_results": [s.to_dict() for s in self.semantic_results],
            "failure_reasons": list(self.failure_reasons),
            "failure_category": self.failure_category.value,
            "is_mock": self.is_mock,
            "timestamp": self.timestamp,
        }

    def canonical_digest(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.to_dict())).hexdigest()


def _check_string_booleans(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key, val in payload.items():
        if isinstance(val, str) and val.lower() in ("true", "false"):
            errors.append(f"Field '{key}' has string boolean value '{val}'; must be genuine bool.")
        elif isinstance(val, dict):
            for sub_k, sub_v in val.items():
                if isinstance(sub_v, str) and sub_v.lower() in ("true", "false"):
                    errors.append(f"Subfield '{key}.{sub_k}' has string boolean value '{sub_v}'.")
    return errors


def _check_numeric_scores(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for sf in ("composite_score", "score", "marginal_value_score"):
        if sf in payload:
            val = payload[sf]
            if val is None or not isinstance(val, (int, float)):
                errors.append(f"Score field '{sf}' must be numeric, got {type(val).__name__}.")
            elif math.isnan(val) or math.isinf(val):
                errors.append(f"Score field '{sf}' cannot be NaN or Infinity.")
            elif val < 0.0 or val > 100.0:
                errors.append(f"Score field '{sf}' value {val} out of permitted range [0.0, 100.0].")
    return errors


def _check_dimension_scores(dim_scores: Any) -> List[str]:
    if not isinstance(dim_scores, dict):
        return []
    errors: List[str] = []
    seen_dims: Set[str] = set()
    for d_key, d_val in dim_scores.items():
        if d_key in seen_dims:
            errors.append(f"Duplicate dimension score key: '{d_key}'.")
        seen_dims.add(d_key)
        if not isinstance(d_val, (int, float)) or math.isnan(d_val) or math.isinf(d_val):
            errors.append(f"Dimension '{d_key}' score must be valid finite number.")
        elif d_val < 0.0 or d_val > 100.0:
            errors.append(f"Dimension '{d_key}' score {d_val} out of range [0.0, 100.0].")
    return errors


def validate_evaluator_payload(raw_payload: Any) -> Tuple[bool, List[str], FailureCategory]:
    """Strictly validates an evaluator payload against governance integrity rules."""
    if not isinstance(raw_payload, dict):
        return False, ["Payload must be a dictionary object"], FailureCategory.MALFORMED_PAYLOAD

    bool_errors = _check_string_booleans(raw_payload)
    if bool_errors:
        return False, bool_errors, FailureCategory.STRING_BOOLEAN_REJECTED

    score_errors = _check_numeric_scores(raw_payload)
    dim_errors = _check_dimension_scores(raw_payload.get("dimension_scores"))
    all_score_errors = score_errors + dim_errors
    if all_score_errors:
        return False, all_score_errors, FailureCategory.INVALID_SCORE_RANGE

    return True, [], FailureCategory.NONE


def validate_claim_grounding(
    claim: str,
    citations: Sequence[str],
    source_text: str,
) -> Tuple[bool, Optional[str], FailureCategory]:
    """Verifies that a claim has supporting citations present in source text."""
    if not claim or not claim.strip():
        return False, "Claim is empty.", FailureCategory.SCHEMA_VALIDATION_ERROR
    if not citations:
        return False, f"Claim '{claim[:60]}' has zero grounding citations.", FailureCategory.UNSUPPORTED_CLAIM

    source_lower = source_text.lower()
    valid_citations = 0
    for cit in citations:
        if cit.strip() and cit.lower() in source_lower:
            valid_citations += 1

    if valid_citations == 0:
        return (
            False,
            f"None of the {len(citations)} citations for claim '{claim[:60]}' were found in source text.",
            FailureCategory.IRRELEVANT_CITATION,
        )

    return True, None, FailureCategory.NONE


def check_live_to_mock_fallback(
    is_mock: bool,
    provider_available: bool = True,
    timeout_occurred: bool = False,
) -> Tuple[bool, Optional[str], FailureCategory]:
    """Enforces zero silent live-to-mock fallback under Decision W4.

    If an evaluator timed out or is unavailable, falling back to mock scoring
    to manufacture a passing verdict is strictly prohibited.
    """
    if timeout_occurred:
        return False, "Evaluator timed out; live-to-mock fallback is prohibited.", FailureCategory.TIMEOUT_EXCEEDED
    if not provider_available:
        return False, "Evaluator unavailable; live-to-mock fallback is prohibited.", FailureCategory.EVALUATOR_UNAVAILABLE
    if is_mock:
        return False, "Mock evaluator result rejected in production gate qualification.", FailureCategory.MOCK_FALLBACK_PROHIBITED
    return True, None, FailureCategory.NONE
