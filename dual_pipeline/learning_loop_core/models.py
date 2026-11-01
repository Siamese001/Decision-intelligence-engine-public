"""
dual_pipeline/learning_loop_core/models.py

Data models, proposal category constants, and parameter bounds for the
closed-loop answer learning system (Wave 8).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

CATEGORY_COPILOT_CONTEXT: str = "copilot_context"
CATEGORY_COPILOT_GROUNDING: str = "copilot_grounding"
CATEGORY_QUERY_RETRIEVAL: str = "query_retrieval"

ALL_ANSWER_LEARNING_CATEGORIES: Tuple[str, ...] = (
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_COPILOT_GROUNDING,
    CATEGORY_QUERY_RETRIEVAL,
)

# Canonical parameter defaults and safe step adjustments for automatic proposals
CATEGORY_PARAMETER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    CATEGORY_COPILOT_CONTEXT: {
        "primary_parameter": "copilot.context.decay_tau_seconds",
        "default_step": 150.0,
        "parameters": [
            "copilot.context.decay_tau_seconds",
            "copilot.context.focus_seconds",
            "copilot.context.salient_entities_top_k",
            "copilot.context.flow_items_max",
            "copilot.context.focus_weight",
        ],
    },
    CATEGORY_COPILOT_GROUNDING: {
        "primary_parameter": "copilot.rigor.min_provenance_score",
        "default_step": 0.1,
        "parameters": [
            "copilot.rigor.min_provenance_score",
            "copilot.rigor.budget_ms",
            "copilot.neighborhood_max_hops",
            "copilot.rerank.lexical",
            "copilot.rerank.structural",
            "copilot.rerank.dense",
        ],
    },
    CATEGORY_QUERY_RETRIEVAL: {
        "primary_parameter": "retrieval.bm25_weight",
        "default_step": 0.05,
        "parameters": [
            "retrieval.bm25_weight",
            "retrieval.dense_weight",
            "retrieval.colbert_weight",
        ],
    },
}

RETRIEVAL_PARAMETER_BOUNDS: Dict[str, Tuple[float, float, float]] = {
    "retrieval.bm25_weight": (0.0, 1.0, 0.5),
    "retrieval.dense_weight": (0.0, 1.0, 0.5),
    "retrieval.colbert_weight": (0.0, 1.0, 0.0),
    "retrieval.route_multiplier.relational": (0.20, 0.70, 0.45),
    "retrieval.route_multiplier.temporal": (0.20, 0.70, 0.45),
    "retrieval.route_multiplier.strategic": (0.20, 0.70, 0.45),
    "retrieval.route_multiplier.analytical": (0.20, 0.70, 0.45),
    "retrieval.route_multiplier.concept": (0.20, 0.70, 0.40),
    "retrieval.route_multiplier.contradiction": (0.20, 0.70, 0.45),
    "retrieval.route_multiplier.entity_anchor": (0.20, 0.70, 0.45),
}


@dataclass
class AnswerFailureItem:
    """Individual failure observation extracted from an eval receipt or ledger feedback."""

    source: str
    receipt_id: str
    question_id: str
    pipeline: str
    failure_type: str
    metric_value: float
    threshold: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerFailureCohort:
    """Group of recurring failure patterns meeting the clustering threshold (>=2 occurrences)."""

    cohort_key: str
    category: str
    occurrence_count: int
    run_ids: List[str]
    receipt_ids: List[str]
    items: List[Dict[str, Any]]
    target_parameter: str
    current_value: Any
    proposed_value: Any
    latest_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerSimulationResult:
    """Outcome of dry-run replay simulation evaluating proposed parameter changes."""

    proposal_id: str
    success: bool
    status: str
    target_metric_before: float
    target_metric_after: float
    guard_metrics_passed: bool
    latency_within_budget: bool
    bge_m3_gates_passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    simulated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    proposal_digest: str = ""
    replay_digest: str = ""

    @property
    def metrics(self) -> Dict[str, Any]:
        return self.details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "success": self.success,
            "status": self.status,
            "target_metric_before": self.target_metric_before,
            "target_metric_after": self.target_metric_after,
            "guard_metrics_passed": self.guard_metrics_passed,
            "latency_within_budget": self.latency_within_budget,
            "bge_m3_gates_passed": self.bge_m3_gates_passed,
            "details": self.details,
            "simulated_at": self.simulated_at,
            "proposal_digest": self.proposal_digest,
            "replay_digest": self.replay_digest,
        }


@dataclass
class LearningChangeProposal:
    """Typed wrapper for a schema-valid learning-change-proposal:1.0.0 artifact."""

    proposal_id: str
    proposal_type: str
    problem: str
    proposed_change: str
    evidence_event_ids: List[str]
    run_ids: List[str] = field(default_factory=lambda: ["run-learning-default"])
    status: str = "proposed"
    risk: str = "low"
    approval_required: bool = True
    auto_apply: bool = False
    extensions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
