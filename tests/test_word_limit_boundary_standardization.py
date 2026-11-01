"""tests/test_word_limit_boundary_standardization.py

Acceptance tests for Wave 2 word-limit boundary alignments:
- Engineering output counter & verifier loading from parameters SSOT
- Implementation summary validator messages
- Wave plan scaling bounds from SSOT
- Direct answer gate consistent <= word limit across routes
- Grounding metrics inclusive <= comparison and lineage footer exclusion
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.grounding.metrics import (
    count_words,
    evaluate_text_metrics,
    extract_prompt_word_ceiling,
    strip_evidence_lineage,
)
from tools.direct_answer_gate import validate_direct_answer
from tools.engineering_output.counter import (
    MAX_ENGINEERING_CHARS,
    MAX_ENGINEERING_WORDS,
    MAX_PROGRESS_WORDS,
    validate_progress_update,
    validate_text_limits,
)
from tools.implementation_plan_core.evidence_requirements import (
    MAX_PLAN_WORDS,
    compute_max_plan_words,
)
from tools.validate_implementation_summary import validate_summary_content


class MockWave:
    def __init__(self, deliverable_count: int = 0) -> None:
        self.deliverable_items = [f"item_{i}" for i in range(deliverable_count)]
        self.lines: list[str] = []


class TestWordLimitBoundaryStandardization(unittest.TestCase):
    def test_engineering_output_counter_ssot_bounds(self) -> None:
        """Verify engineering output limits conform to 300w / 3500c / 20w SSOT."""
        self.assertEqual(MAX_ENGINEERING_WORDS, 300)
        self.assertEqual(MAX_ENGINEERING_CHARS, 3500)
        self.assertEqual(MAX_PROGRESS_WORDS, 20)

        # 300 words passes, 301 words fails
        text_300 = " ".join(["word"] * 300)
        text_301 = " ".join(["word"] * 301)
        self.assertEqual(validate_text_limits(text_300), [])
        errs_301 = validate_text_limits(text_301)
        self.assertTrue(any("exceeds maximum allowed ceiling of 300 words" in e for e in errs_301))

        # Progress update 20 words passes, 21 fails
        prog_20 = "Ran verification in local sandbox and confirmed that all unit tests passed with zero unexpected errors observed."
        self.assertLessEqual(len(prog_20.split()), 20)
        res_20 = validate_progress_update(prog_20)
        self.assertTrue(res_20.is_valid)

        prog_21 = "Ran verification in local sandbox and confirmed that all unit tests passed with zero unexpected errors observed during this execution turn."
        self.assertGreater(len(prog_21.split()), 20)
        res_21 = validate_progress_update(prog_21)
        self.assertFalse(res_21.is_valid)
        self.assertTrue(any("exceeds maximum ceiling of 20 words" in e for e in res_21.errors))

    def test_implementation_summary_validator_message(self) -> None:
        """Verify summary validator failure message cites <=300 words and <=3500 chars."""
        # Non-compliant summary lacking required environment/action/table
        is_valid, errors, _ = validate_summary_content("Incomplete summary text.")
        self.assertFalse(is_valid)
        self.assertTrue(
            any("<=300 words, <=3500 chars" in e for e in errors),
            f"Expected updated limits in errors, got: {errors}",
        )
        self.assertFalse(
            any("<=1400 chars" in e for e in errors),
            f"Stale 1400 chars limit still present in errors: {errors}",
        )

    def test_plan_word_limits_ssot_scaling(self) -> None:
        """Verify plan word limits scale dynamically from SSOT parameters."""
        self.assertEqual(MAX_PLAN_WORDS, 1000)

        # 2 waves with 4 deliverables each: 400 + 250*2 + 25*8 = 1100 words
        waves = [MockWave(4), MockWave(4)]
        self.assertEqual(compute_max_plan_words(waves), 1100)

    def test_direct_answer_gate_standardized_bounds(self) -> None:
        """Verify direct_answer_gate enforces inclusive <= word_limit across routes."""
        # Route: query with exactly 30 words against word_limit=30
        words_30 = " ".join(["token"] * 30)
        res_query_pass = validate_direct_answer(words_30, route="query", word_limit=30)
        self.assertEqual(res_query_pass["status"], "PASS")

        # Route: query with 31 words against word_limit=30
        words_31 = " ".join(["token"] * 31)
        res_query_fail = validate_direct_answer(words_31, route="query", word_limit=30)
        self.assertEqual(res_query_fail["status"], "FAIL")
        self.assertTrue(any("at most 30 words" in issue for issue in res_query_fail["issues"]))

        # Route: copilot with exactly 35 words against word_limit=35
        copilot_text_35 = "```text\n" + (" ".join(["token"] * 35)) + "\n```"
        res_copilot_pass = validate_direct_answer(copilot_text_35, route="copilot", word_limit=35)
        self.assertEqual(res_copilot_pass["status"], "PASS")

        copilot_text_36 = "```text\n" + (" ".join(["token"] * 36)) + "\n```"
        res_copilot_fail = validate_direct_answer(copilot_text_36, route="copilot", word_limit=35)
        self.assertEqual(res_copilot_fail["status"], "FAIL")
        self.assertTrue(any("at most 35 words" in issue for issue in res_copilot_fail["issues"]))

    def test_direct_answer_gate_strips_query_lineage_footer(self) -> None:
        """Verify direct_answer_gate excludes ### Evidence & Graph Lineage from query counting."""
        prose_20 = " ".join(["insight"] * 20)
        lineage_table = (
            "\n\n### Evidence & Graph Lineage\n"
            "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| transcript_01 | entity_a, entity_b | ast_1, ast_2, ast_3 | 0.95 |\n"
        )
        full_query_answer = prose_20 + lineage_table

        # Total text is ~45 words, but prose is 20 words. Against word_limit=25, it must PASS.
        res = validate_direct_answer(full_query_answer, route="query", word_limit=25)
        self.assertEqual(res["status"], "PASS")

    def test_grounding_metrics_lineage_stripping_and_inclusive_bounds(self) -> None:
        """Verify evaluate_text_metrics strips lineage and enforces <= limit."""
        prose_450 = " ".join(["statement"] * 450)
        lineage_100 = (
            "\n\n### Evidence & Graph Lineage\n"
            + ("extra lineage metadata word " * 20)
        )
        payload = prose_450 + lineage_100

        # Total payload words > 500, but prose alone is 450 words -> PASS against max_words=500
        res = evaluate_text_metrics(payload, max_words=500)
        self.assertTrue(res.passed_count)
        self.assertEqual(res.word_count, 450)

        # Prompt with dynamic ceiling 'under 460 words'
        res_prompt = evaluate_text_metrics(payload, max_words=500, prompt_text="Please provide answer under 460 words.")
        self.assertTrue(res_prompt.passed_count)

        # Prompt with dynamic ceiling 'at most 400 words' -> FAILS (450 > 400)
        res_prompt_fail = evaluate_text_metrics(payload, max_words=500, prompt_text="Provide at most 400 words.")
        self.assertFalse(res_prompt_fail.passed_count)
        self.assertTrue(any("violates strict limit <= 400" in r for r in res_prompt_fail.reasons))


if __name__ == "__main__":
    unittest.main()
