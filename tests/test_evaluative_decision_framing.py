"""Wave 1 tests for evaluative and counterfactual decision framing."""

from __future__ import annotations

import unittest

from dual_pipeline.decision_engine import build_decision_frame
from dual_pipeline.decision_engine_core.constants import (
    DEFAULT_EVALUATIVE_CRITERIA,
    DEFAULT_EVALUATIVE_OPTIONS,
    extract_criteria,
)


class EvaluativeDecisionFramingTests(unittest.TestCase):
    def test_evaluative_pod_prompt_builds_ready_frame_without_magic_prepositions(self) -> None:
        question = (
            "provide honest analysis of Brad/Beau discussion on pods local to West Palm Beach "
            "area from the perspective of NEE - what might work and what will not work and "
            "explain why - give balanced view"
        )
        frame = build_decision_frame(question=question)
        self.assertEqual("READY", frame["frame_state"])
        self.assertEqual([], frame["missing_fields"])
        self.assertEqual(
            list(DEFAULT_EVALUATIVE_CRITERIA),
            [row["label"] for row in frame["criteria"]],
        )
        self.assertEqual(
            list(DEFAULT_EVALUATIVE_OPTIONS),
            [row["label"] for row in frame["options"]],
        )

    def test_what_if_and_pros_cons_prompts_bind_default_evaluative_criteria(self) -> None:
        for prompt in (
            "What-if we deploy 10 local West Palm Beach pods into Apex Global Analytics?",
            "Evaluate the pros and cons of shared domain pods under outcome pricing.",
            "Assess the feasibility and viability of local pod staffing in Juno Beach.",
        ):
            with self.subTest(prompt=prompt):
                frame = build_decision_frame(question=prompt)
                self.assertEqual("READY", frame["frame_state"])
                self.assertEqual(
                    list(DEFAULT_EVALUATIVE_CRITERIA),
                    [row["label"] for row in frame["criteria"]],
                )

    def test_explicit_criteria_and_preposition_clauses_take_precedence(self) -> None:
        frame_with_clause = build_decision_frame(
            question="What might work for local pods given speed, margin, and retention?"
        )
        self.assertEqual("READY", frame_with_clause["frame_state"])
        self.assertEqual(
            ["speed", "margin", "retention"],
            [row["label"] for row in frame_with_clause["criteria"]],
        )

        frame_with_caller = build_decision_frame(
            question="What might work and what will not work for local pods?",
            options=["3-5 Pod Pilot", "10-Pod Rollout"],
            criteria=["screening_yield", "necap_eligibility"],
        )
        self.assertEqual(
            ["3-5 Pod Pilot", "10-Pod Rollout"],
            [row["label"] for row in frame_with_caller["options"]],
        )
        self.assertEqual(
            ["screening_yield", "necap_eligibility"],
            [row["label"] for row in frame_with_caller["criteria"]],
        )

    def test_unframed_generic_selection_still_requires_clarification(self) -> None:
        for prompt in (
            "Which option should NEE choose?",
            "Compare NEE evidence across options and recommend which to choose",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual([], extract_criteria(prompt))
                frame = build_decision_frame(question=prompt)
                self.assertEqual("CLARIFICATION_REQUIRED", frame["frame_state"])
                self.assertEqual(["criteria"], frame["missing_fields"])


if __name__ == "__main__":
    unittest.main()
