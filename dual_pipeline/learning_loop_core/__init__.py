"""
dual_pipeline/learning_loop_core

Modular closed-loop learning engine supporting copilot_context,
copilot_grounding, and query_retrieval learning proposals (Wave 8).
"""
from __future__ import annotations

from .models import (
    ALL_ANSWER_LEARNING_CATEGORIES,
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_COPILOT_GROUNDING,
    CATEGORY_PARAMETER_DEFAULTS,
    CATEGORY_QUERY_RETRIEVAL,
    RETRIEVAL_PARAMETER_BOUNDS,
    AnswerFailureCohort,
    AnswerFailureItem,
    AnswerSimulationResult,
)
from .scanner import (
    scan_answer_eval_receipts,
    synthesize_answer_learning_proposal,
)
from .seal import (
    CANARY_STAGE,
    LEARNING_STAGES,
    LEARNING_STAGES_WITH_CANARY,
    record_canary_learning_event,
    record_learning_event,
    seal_learning_stage_chain,
)
from .simulation import (
    simulate_answer_learning_proposal,
)
from .uwg_bridge import (
    apply_answer_learning_proposal,
    rollback_answer_learning_proposal,
)

__all__ = [
    "ALL_ANSWER_LEARNING_CATEGORIES",
    "CATEGORY_COPILOT_CONTEXT",
    "CATEGORY_COPILOT_GROUNDING",
    "CATEGORY_PARAMETER_DEFAULTS",
    "CATEGORY_QUERY_RETRIEVAL",
    "RETRIEVAL_PARAMETER_BOUNDS",
    "AnswerFailureCohort",
    "AnswerFailureItem",
    "AnswerSimulationResult",
    "scan_answer_eval_receipts",
    "synthesize_answer_learning_proposal",
    "simulate_answer_learning_proposal",
    "apply_answer_learning_proposal",
    "rollback_answer_learning_proposal",
    "CANARY_STAGE",
    "LEARNING_STAGES",
    "LEARNING_STAGES_WITH_CANARY",
    "record_canary_learning_event",
    "record_learning_event",
    "seal_learning_stage_chain",
]
