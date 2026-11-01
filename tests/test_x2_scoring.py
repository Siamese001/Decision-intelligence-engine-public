"""Unit tests for Phase 6 — X2 Gate Materiality and Epistemic Scoring."""
from __future__ import annotations

import unittest


class TestX2ScoringLogic(unittest.TestCase):
    def compute_x2_scoring(
        self,
        *,
        approved_count: int,
        candidate_count: int,
        external_count: int = 0,
        contradiction_count: int = 0,
        coverage_complete: bool = True,
        materiality: str = "medium",
        evidence_ready: bool = True,
    ) -> dict:
        total_evidence = approved_count + candidate_count + external_count
        if total_evidence > 0:
            approved_ratio = approved_count / total_evidence
            contradiction_penalty = min(0.5, 0.25 * contradiction_count)
            epistemic_score = max(0.0, min(1.0, approved_ratio - contradiction_penalty))
        else:
            epistemic_score = 1.0 if evidence_ready else 0.0
            approved_ratio = 1.0
            contradiction_penalty = 0.0

        if contradiction_count > 0:
            epistemic_tier = "EPISTEMICALLY_DISPUTED"
        elif candidate_count > 0:
            epistemic_tier = "QUALIFIED_HYPOTHESIS"
        else:
            epistemic_tier = "VERIFIED_FACTUAL"

        mat_weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(materiality, 0.3)
        coverage_factor = 1.0 if coverage_complete else 0.5
        materiality_score = round(mat_weight * coverage_factor * epistemic_score, 4)

        return {
            "materiality_score": materiality_score,
            "epistemic_score": round(epistemic_score, 4),
            "epistemic_tier": epistemic_tier,
            "scoring_receipt": {
                "materiality_weight": mat_weight,
                "coverage_factor": coverage_factor,
                "approved_ratio": round(approved_ratio, 4),
                "contradiction_penalty": contradiction_penalty,
            },
        }

    def test_pure_approved_high_materiality(self) -> None:
        res = self.compute_x2_scoring(
            approved_count=5,
            candidate_count=0,
            contradiction_count=0,
            coverage_complete=True,
            materiality="high",
        )
        self.assertEqual(res["epistemic_score"], 1.0)
        self.assertEqual(res["epistemic_tier"], "VERIFIED_FACTUAL")
        self.assertEqual(res["materiality_score"], 1.0)
        self.assertEqual(res["scoring_receipt"]["contradiction_penalty"], 0.0)

    def test_candidate_hypothesis_tier(self) -> None:
        res = self.compute_x2_scoring(
            approved_count=3,
            candidate_count=2,
            contradiction_count=0,
            coverage_complete=True,
            materiality="medium",
        )
        self.assertEqual(res["epistemic_tier"], "QUALIFIED_HYPOTHESIS")
        self.assertAlmostEqual(res["epistemic_score"], 0.6)
        self.assertAlmostEqual(res["materiality_score"], round(0.6 * 1.0 * 0.6, 4))

    def test_contradiction_penalty(self) -> None:
        res = self.compute_x2_scoring(
            approved_count=4,
            candidate_count=0,
            contradiction_count=1,
            coverage_complete=True,
            materiality="high",
        )
        self.assertEqual(res["epistemic_tier"], "EPISTEMICALLY_DISPUTED")
        self.assertAlmostEqual(res["epistemic_score"], 0.75)  # 1.0 - 0.25
        self.assertAlmostEqual(res["materiality_score"], 0.75)

    def test_incomplete_coverage_penalty(self) -> None:
        res = self.compute_x2_scoring(
            approved_count=5,
            candidate_count=0,
            contradiction_count=0,
            coverage_complete=False,
            materiality="high",
        )
        self.assertEqual(res["materiality_score"], 0.5)  # 1.0 * 0.5 * 1.0


if __name__ == "__main__":
    unittest.main()
