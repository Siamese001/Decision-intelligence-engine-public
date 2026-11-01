"""Independent Standard IR Evaluator for Wave 1 Calibration.

Implements reference TREC-compliant reciprocal rank, graded nDCG with fixed
query judgments, recall, and no-answer rejection rate completely independently
of the production pipeline for mathematical cross-verification.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence, Set


class IndependentStandardEvaluator:
    """Reference standard evaluator for retrieval qualification."""

    def __init__(
        self,
        top_k: int = 10,
        relevance_threshold: int = 1,
        gain_mapping: str = "exponential",
    ) -> None:
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        self.gain_mapping = gain_mapping

    def validate_ranking(self, record_ids: Sequence[str]) -> list[str]:
        """Validate well-formedness and reject duplicate record IDs."""
        if not isinstance(record_ids, (list, tuple)):
            raise TypeError(f"record_ids must be a sequence, got {type(record_ids).__name__}")
        slice_ids = list(record_ids[: self.top_k])
        seen: set[str] = set()
        duplicates: list[str] = []
        for rid in slice_ids:
            if not isinstance(rid, str) or not rid.strip():
                raise ValueError(f"Invalid record ID: {rid!r}")
            if rid in seen:
                duplicates.append(rid)
            seen.add(rid)
        if duplicates:
            raise ValueError(f"Duplicate record IDs detected in retrieved ranking: {duplicates}")
        return slice_ids

    def reciprocal_rank(
        self,
        retrieved_ids: Sequence[str],
        query_judgments: Mapping[str, int] | Set[str],
    ) -> float | None:
        """TREC Reciprocal Rank at cutoff k with explicit relevance threshold."""
        valid_ids = self.validate_ranking(retrieved_ids)
        judg_map = self._to_map(query_judgments)

        has_positives = any(g >= self.relevance_threshold for g in judg_map.values())
        if not has_positives:
            return None

        for rank, rid in enumerate(valid_ids, 1):
            if judg_map.get(rid, 0) >= self.relevance_threshold:
                return 1.0 / rank
        return 0.0

    def ndcg_at_k(
        self,
        retrieved_ids: Sequence[str],
        query_judgments: Mapping[str, int] | Set[str],
    ) -> float | None:
        """Graded nDCG@k using fixed query judgments for ideal DCG."""
        valid_ids = self.validate_ranking(retrieved_ids)
        judg_map = self._to_map(query_judgments)

        pos_grades = [g for g in judg_map.values() if g > 0]
        if not pos_grades:
            return None

        def gain(g: int) -> float:
            return (2.0**g - 1.0) if self.gain_mapping == "exponential" else float(g)

        dcg = sum(
            gain(judg_map.get(rid, 0)) / math.log2(rank + 1)
            for rank, rid in enumerate(valid_ids, 1)
            if judg_map.get(rid, 0) > 0
        )
        ideal_grades = sorted(pos_grades, reverse=True)[: self.top_k]
        idcg = sum(gain(g) / math.log2(rank + 1) for rank, g in enumerate(ideal_grades, 1))
        return float(dcg / idcg) if idcg > 0.0 else None

    def recall_at_k(
        self,
        retrieved_ids: Sequence[str],
        query_judgments: Mapping[str, int] | Set[str],
    ) -> float | None:
        """Recall@k against fixed positive judgments in ground truth."""
        valid_ids = self.validate_ranking(retrieved_ids)
        judg_map = self._to_map(query_judgments)

        positives = {rid for rid, g in judg_map.items() if g >= self.relevance_threshold}
        if not positives:
            return None
        return len(set(valid_ids) & positives) / len(positives)

    def _evaluate_positive(self, retrieved: list[str], judg_map: Mapping[str, int], pos_mrrs: list[float], pos_ndcgs: list[float], pos_recalls: list[float]) -> None:
        rr = self.reciprocal_rank(retrieved, judg_map)
        ndcg = self.ndcg_at_k(retrieved, judg_map)
        recall = self.recall_at_k(retrieved, judg_map)
        if rr is not None:
            pos_mrrs.append(rr)
        if ndcg is not None:
            pos_ndcgs.append(ndcg)
        if recall is not None:
            pos_recalls.append(recall)

    def evaluate_dataset(
        self,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate evaluation isolating positive queries from no-answer queries."""
        if not cases:
            return {
                "case_count": 0,
                "positive_case_count": 0,
                "recall_at_k": 0.0,
                "mrr": 0.0,
                "ndcg_at_k": 0.0,
                "no_answer_case_count": 0,
                "no_answer_rejection_rate": 0.0,
                "status": "FAIL_EMPTY_DATASET",
            }

        pos_recalls: list[float] = []
        pos_mrrs: list[float] = []
        pos_ndcgs: list[float] = []
        no_answer_count = 0
        no_answer_rejections = 0

        for case in cases:
            retrieved = case.get("retrieved", [])
            judgments = case.get("judgments", {})
            judg_map = self._to_map(judgments)

            is_positive = any(g >= self.relevance_threshold for g in judg_map.values())
            if is_positive:
                self._evaluate_positive(retrieved, judg_map, pos_mrrs, pos_ndcgs, pos_recalls)
            else:
                no_answer_count += 1
                rejected = len(retrieved) == 0 or all(
                    judg_map.get(rid, 0) < self.relevance_threshold for rid in retrieved
                )
                if rejected:
                    no_answer_rejections += 1

        return {
            "case_count": len(cases),
            "positive_case_count": len(pos_mrrs),
            "recall_at_k": sum(pos_recalls) / len(pos_recalls) if pos_recalls else 0.0,
            "mrr": sum(pos_mrrs) / len(pos_mrrs) if pos_mrrs else 0.0,
            "ndcg_at_k": sum(pos_ndcgs) / len(pos_ndcgs) if pos_ndcgs else 0.0,
            "no_answer_case_count": no_answer_count,
            "no_answer_rejection_rate": (
                no_answer_rejections / no_answer_count if no_answer_count > 0 else 1.0
            ),
        }

    @staticmethod
    def _to_map(judgments: Mapping[str, int] | Set[str]) -> Mapping[str, int]:
        if isinstance(judgments, (set, frozenset)):
            return {rid: 1 for rid in judgments}
        if isinstance(judgments, Mapping):
            return judgments
        raise TypeError(f"judgments must be Mapping or Set, got {type(judgments).__name__}")
