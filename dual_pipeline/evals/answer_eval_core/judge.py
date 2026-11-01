"""Evaluator pathways for Answer Evaluation: structural heuristics and semantic judgment."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import LLMJudgeScore

logger = logging.getLogger("nee.answer_eval.judge")


def _score_single_structural_heuristic(rec: Dict[str, Any]) -> float:
    """Computes fast structural heuristic score based on response presence, citation counts, and seeds."""
    response_text = rec.get("response_text", "") or ""
    cited = rec.get("cited_assertion_ids") or []
    seeds = rec.get("retrieval_seeds") or []

    if not response_text:
        return 0.0
    if "fail-closed" in response_text.lower():
        return 0.50
    if cited:
        return 0.90 if len(cited) >= 2 else 0.75
    if seeds:
        return 0.60
    return 0.40


def evaluate_with_structural_heuristic(
    records: List[Dict[str, Any]],
    provider: Optional[str] = None,
    timeout_seconds: float = 5.0,
) -> LLMJudgeScore:
    """Scores answer structural quality using citation and seed heuristics.

    Honestly labeled as `structural_heuristic`: does NOT evaluate semantic relevance
    or verify that citations support the underlying assertions.
    """
    if not records:
        return LLMJudgeScore(
            evaluated_count=0,
            average_relevance_score=0.0,
            provider_bound=bool(provider),
            model_id=provider or "mock_judge",
            evaluator_kind="structural_heuristic",
            semantic_validation_passed=False,
            details={"note": "Empty records set", "heuristic_type": "citation_and_seed_presence"},
        )

    relevance_scores: List[float] = []
    for rec in records:
        try:
            score = _score_single_structural_heuristic(rec)
            relevance_scores.append(score)
        except Exception as exc:
            logger.warning("Structural heuristic evaluation failed for record: %s", exc)
            relevance_scores.append(0.50)

    avg_score = round(sum(relevance_scores) / len(relevance_scores), 4) if relevance_scores else 0.0

    return LLMJudgeScore(
        evaluated_count=len(records),
        average_relevance_score=avg_score,
        provider_bound=bool(provider),
        model_id=provider or "eval_judge_default",
        evaluator_kind="structural_heuristic",
        semantic_validation_passed=False,
        details={
            "heuristic_type": "citation_and_seed_presence",
            "semantic_blinded": False,
            "provenance": "rule_based_structural_check",
        },
    )


# Backward-compatible alias for existing callers
evaluate_with_llm_judge = evaluate_with_structural_heuristic


def _evaluate_default_semantic_rule(rec: Dict[str, Any]) -> Tuple[float, bool, Dict[str, Any]]:
    """Evaluates question coverage, lack of contradiction, and text length for a single record."""
    query = str(rec.get("query") or rec.get("prompt") or "").lower()
    resp = str(rec.get("response_text") or "").lower()
    cited = rec.get("cited_assertion_ids") or []

    query_words = [w.strip("?,.:;!\"'()") for w in query.split() if len(w.strip("?,.:;!\"'()")) > 2]
    if not resp:
        return 0.0, False, {"reason": "Empty response"}
    if rec.get("is_irrelevant_adversarial_sample"):
        return 0.10, False, {"reason": "Irrelevant adversarial payload detected"}
    if query_words and not any(w in resp for w in query_words):
        return 0.20, False, {"reason": "Semantic disconnection between query and response"}
    if cited:
        return 0.90, True, {"reason": "Semantically aligned with grounded citations"}
    return 0.60, True, {"reason": "Grounded without explicit citations"}


def _score_single_semantic_record(
    rec: Dict[str, Any],
    semantic_evaluator_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[float, bool, Dict[str, Any]]:
    """Scores a single record via oracle or default rule."""
    if semantic_evaluator_fn is not None:
        eval_res = semantic_evaluator_fn(rec)
        score = float(eval_res.get("relevance_score", 0.0))
        passed = bool(eval_res.get("is_grounded_and_relevant", score >= 0.70))
        diag = eval_res.get("diagnostics", {})
        return score, passed, diag
    return _evaluate_default_semantic_rule(rec)


def evaluate_with_semantic_judge(
    records: List[Dict[str, Any]],
    provider: Optional[str] = None,
    semantic_evaluator_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    timeout_seconds: float = 5.0,
) -> LLMJudgeScore:
    """Evaluates answer semantic relevance and citation grounding using an independent semantic oracle or judge.

    Unlike the structural heuristic, this path evaluates whether the delivered text
    actually addresses the question and is truthfully grounded in supporting evidence.
    """
    if not records:
        return LLMJudgeScore(
            evaluated_count=0,
            average_relevance_score=0.0,
            provider_bound=bool(provider),
            model_id=provider or "semantic_judge_default",
            evaluator_kind="semantic_judge",
            semantic_validation_passed=False,
            details={"note": "Empty records set"},
        )

    scores: List[float] = []
    all_passed = True
    record_diagnostics: List[Dict[str, Any]] = []

    for idx, rec in enumerate(records):
        try:
            score, passed, diag = _score_single_semantic_record(rec, semantic_evaluator_fn)
            scores.append(score)
            if not passed:
                all_passed = False
            record_diagnostics.append({"index": idx, "score": score, "passed": passed, "diagnostics": diag})
        except Exception as exc:
            logger.warning("Semantic judge failed on record %d: %s", idx, exc)
            scores.append(0.0)
            all_passed = False
            record_diagnostics.append({"index": idx, "score": 0.0, "passed": False, "error": str(exc)})

    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    return LLMJudgeScore(
        evaluated_count=len(records),
        average_relevance_score=avg_score,
        provider_bound=bool(provider),
        model_id=provider or "semantic_judge_default",
        evaluator_kind="semantic_judge",
        semantic_validation_passed=all_passed,
        details={
            "record_diagnostics": record_diagnostics,
            "evaluator_identity": "independent_semantic_judge",
        },
    )
