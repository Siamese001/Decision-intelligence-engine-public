"""Answer Evaluation Core package."""
from .harness import AnswerEvalHarness
from .judge import evaluate_with_llm_judge
from .metrics import (
    compute_aggregate_metrics,
    compute_citation_durability,
    compute_context_recall,
    compute_fail_closed_rate,
    compute_groundedness,
    compute_latency_stats,
    compute_operator_usefulness,
    compute_post_meeting_uptake,
    compute_staleness_and_conflict_miss,
)
from .models import (
    AnswerEvalReceipt,
    LatencyStats,
    OperatorUsefulnessStats,
    ReplayComparison,
    compute_eval_receipt_digest,
    validate_eval_receipt,
)
from .replay import replay_candidate_parameters

__all__ = [
    "AnswerEvalHarness",
    "AnswerEvalReceipt",
    "LatencyStats",
    "OperatorUsefulnessStats",
    "ReplayComparison",
    "compute_aggregate_metrics",
    "compute_citation_durability",
    "compute_context_recall",
    "compute_eval_receipt_digest",
    "compute_fail_closed_rate",
    "compute_groundedness",
    "compute_latency_stats",
    "compute_operator_usefulness",
    "compute_post_meeting_uptake",
    "compute_staleness_and_conflict_miss",
    "evaluate_with_llm_judge",
    "replay_candidate_parameters",
    "validate_eval_receipt",
]
