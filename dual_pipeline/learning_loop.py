"""Top-level facade for the NEE Closed-Loop System Learning Loop.

Exposes canonical models, deterministic failure cohort clustering across
RETRIEVAL_MISS, GROUNDING_REJECTION, JUDGE_RETRY_EXHAUSTION, OWNER_DRIFT,
and TEMPORAL_STALENESS, dry-run holdout simulation, and 6-stage Merkle sealing.
"""
from __future__ import annotations

from dual_pipeline.learning_loop_core import (
    ALL_ANSWER_LEARNING_CATEGORIES,
    CANARY_STAGE,
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_COPILOT_GROUNDING,
    CATEGORY_PARAMETER_DEFAULTS,
    CATEGORY_QUERY_RETRIEVAL,
    LEARNING_STAGES,
    LEARNING_STAGES_WITH_CANARY,
    RETRIEVAL_PARAMETER_BOUNDS,
    AnswerFailureCohort,
    AnswerFailureItem,
    AnswerSimulationResult,
    apply_answer_learning_proposal,
    record_canary_learning_event,
    record_learning_event,
    rollback_answer_learning_proposal,
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.learning_loop_core.models import LearningChangeProposal
from dual_pipeline.learning_loop_core.scanner import (
    CANONICAL_COHORT_TYPES,
    cluster_failure_cohorts,
    scan_feedback_for_proposals,
)
from dual_pipeline.learning_loop_core.simulation import simulate_proposal

__all__ = [
    "ALL_ANSWER_LEARNING_CATEGORIES",
    "AnswerFailureCohort",
    "AnswerFailureItem",
    "AnswerSimulationResult",
    "CANARY_STAGE",
    "CANONICAL_COHORT_TYPES",
    "CATEGORY_COPILOT_CONTEXT",
    "CATEGORY_COPILOT_GROUNDING",
    "CATEGORY_PARAMETER_DEFAULTS",
    "CATEGORY_QUERY_RETRIEVAL",
    "LEARNING_STAGES",
    "LEARNING_STAGES_WITH_CANARY",
    "LearningChangeProposal",
    "RETRIEVAL_PARAMETER_BOUNDS",
    "apply_answer_learning_proposal",
    "cluster_failure_cohorts",
    "record_canary_learning_event",
    "record_learning_event",
    "rollback_answer_learning_proposal",
    "scan_answer_eval_receipts",
    "scan_feedback_for_proposals",
    "seal_learning_stage_chain",
    "simulate_answer_learning_proposal",
    "simulate_proposal",
    "synthesize_answer_learning_proposal",
]
