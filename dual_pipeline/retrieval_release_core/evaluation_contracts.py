"""Evaluation contracts and consumer separation for retrieval qualification.

Governs the four distinct consumer contracts:
1. Evidence Relevance: Information retrieval effectiveness (Recall@k, MRR@k, nDCG@k).
2. Route Correctness: Query routing into canonical intents with ambiguity gating.
3. Supported Answering: Evidence-grounded claim support vs ungrounded hallucination.
4. Safe Cache Reuse: Exact entity, temporal, permission, and polarity match.
And the dedicated No-Answer Rejection contract for unanswerable queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


class GainMapping(str, Enum):
    EXPONENTIAL = "exponential"  # 2^rel - 1 (standard TREC / MS MARCO)
    LINEAR = "linear"            # rel


class DuplicateBehavior(str, Enum):
    FAIL_CLOSED = "fail_closed"


class UnjudgedBehavior(str, Enum):
    ZERO_GAIN = "zero_gain"


@dataclass(frozen=True)
class EvidenceRelevanceContract:
    """Consumer contract for document/chunk evidence retrieval effectiveness."""

    top_k: int = 10
    relevance_threshold: int = 1
    gain_mapping: GainMapping = GainMapping.EXPONENTIAL
    duplicates_behavior: DuplicateBehavior = DuplicateBehavior.FAIL_CLOSED
    unjudged_behavior: UnjudgedBehavior = UnjudgedBehavior.ZERO_GAIN

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError(f"top_k must be positive, got {self.top_k}")
        if self.relevance_threshold < 1:
            raise ValueError(f"relevance_threshold must be >= 1, got {self.relevance_threshold}")


@dataclass(frozen=True)
class RouteCorrectnessContract:
    """Consumer contract for front-door and semantic intent query routing.

    Entrypoint Separation:
    - Lexical Front-Door Router: dual_pipeline.query_router.route_query
      Handles deterministic keyword, command, and metadata dispatch.
    - Isotropic Semantic Router: dual_pipeline.query_router_core.isotropic_router.IsotropicBgeM3Router
      Handles dense vector embedding intent routing with isotropic whitening.
    """

    ambiguity_margin_threshold: float = field(default_factory=get_hitl_ambiguity_margin_threshold)
    enforce_whitening: bool = True
    canonical_intents: tuple[str, ...] = (
        "exact_lookup",
        "project_status",
        "gap_risk_inconsistency",
        "opportunity_nba",
        "cross_workstream_synthesis",
        "relationship_explanation",
        "candidate_promotion_review",
        "general_assistant_handoff",
    )

    def validate_route(self, intent: str) -> bool:
        return intent in self.canonical_intents


@dataclass(frozen=True)
class SupportedAnsweringContract:
    """Consumer contract for evidence-grounded claim answering."""

    min_grounded_claim_ratio: float = 0.90
    max_unsupported_claim_ratio: float = 0.05
    enforce_abstention_on_unanswerable: bool = True


@dataclass(frozen=True)
class SafeCacheReuseContract:
    """Consumer contract governing safe semantic cache reuse.

    Mandates four immutable gates before cache reuse is authorized:
    1. Exact entity alignment (no entity substitution)
    2. Freshness & temporal alignment
    3. Authorization / tenant isolation boundary match
    4. Negation & polarity match (negated queries never hit positive cache)
    """

    require_exact_entity_match: bool = True
    require_temporal_freshness: bool = True
    require_authorization_match: bool = True
    require_polarity_match: bool = True

    def verify_cache_admissibility(
        self,
        *,
        entity_match: bool,
        temporal_fresh: bool,
        authorized: bool,
        polarity_match: bool,
    ) -> tuple[bool, list[str]]:
        rejections: list[str] = []
        if self.require_exact_entity_match and not entity_match:
            rejections.append("ENTITY_MISMATCH")
        if self.require_temporal_freshness and not temporal_fresh:
            rejections.append("STALE_TEMPORAL_RECORD")
        if self.require_authorization_match and not authorized:
            rejections.append("AUTHORIZATION_BOUNDARY_VIOLATION")
        if self.require_polarity_match and not polarity_match:
            rejections.append("POLARITY_NEGATION_CONFLICT")
        return len(rejections) == 0, rejections


@dataclass(frozen=True)
class NoAnswerRejectionContract:
    """Consumer contract for queries with no expected positive evidence in corpus."""

    min_rejection_rate: float = 0.95
    max_false_retrieval_rate: float = 0.05
    isolate_from_positive_metrics: bool = True


@dataclass(frozen=True)
class RetrievalOperatingLimits:
    """Decision owner operating boundaries fixed before tuning (Wave 1 Gate).

    Declares explicit error ceilings, quality floors, noninferiority margins,
    critical slices, latency budgets, and sample/stopping rules.
    """

    error_ceiling_unsupported: float = 0.05
    min_useful_coverage: float = 0.80
    quality_floor_ndcg_at_10: float = 0.70
    quality_floor_mrr_at_10: float = 0.60
    quality_floor_recall_at_10: float = 0.75
    noninferiority_margin: float = 0.03
    max_p95_latency_ms: float = 250.0
    min_sample_size_per_slice: int = 30
    stopping_rule: str = "fixed_sample_bounded_uncertainty"
    critical_slices: tuple[str, ...] = (
        "leadership_intent",
        "cross_tenant_isolation",
        "stale_superseded_fact",
        "negated_constraint",
    )

    def validate_metrics(
        self,
        *,
        ndcg_10: float,
        mrr_10: float,
        recall_10: float,
        p95_latency_ms: float,
        unsupported_rate: float,
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if ndcg_10 < self.quality_floor_ndcg_at_10:
            failures.append(f"nDCG@10 {ndcg_10:.4f} below floor {self.quality_floor_ndcg_at_10}")
        if mrr_10 < self.quality_floor_mrr_at_10:
            failures.append(f"MRR@10 {mrr_10:.4f} below floor {self.quality_floor_mrr_at_10}")
        if recall_10 < self.quality_floor_recall_at_10:
            failures.append(f"Recall@10 {recall_10:.4f} below floor {self.quality_floor_recall_at_10}")
        if p95_latency_ms > self.max_p95_latency_ms:
            failures.append(f"p95 latency {p95_latency_ms:.1f}ms exceeds budget {self.max_p95_latency_ms}")
        if unsupported_rate > self.error_ceiling_unsupported:
            failures.append(f"Unsupported rate {unsupported_rate:.4f} exceeds ceiling {self.error_ceiling_unsupported}")
        return len(failures) == 0, failures
