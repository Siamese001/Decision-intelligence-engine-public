"""Adversarial and edge-case tests for prompt prefix caching, judge concordance, and token right-sizing."""
from __future__ import annotations

import unittest

from dual_pipeline.transcript_stages_core.external_synthesizer import (
    _build_gemini_generation_config,
    create_gemini_context_cache,
    delete_gemini_context_cache,
)
from dual_pipeline.transcript_stages_core.synthesizers import (
    DEFAULT_FAST_MAX_TOKENS,
    DEFAULT_RECAP_MAX_TOKENS,
    DEFAULT_SYNTHESIS_MAX_TOKENS,
)
from tools.calibrate_judge_tiers import (
    TierEvaluationResult,
    _estimate_tier_cost,
    compute_concordance,
)


class TestJudgeConcordanceAndCachePrefixAdversarial(unittest.TestCase):
    """Adversarial validation of prompt prefix caching, judge tiers, and token caps."""

    def test_prompt_prefix_cache_invariance_forensic(self) -> None:
        """Verify reference transcript strictly precedes candidate in forensic judge prompt."""
        from dual_pipeline.llm_judge_core.judges.forensic import build_forensic_judge_user_content

        ref_text = "Alice: Ground truth meeting text that is long and fixed."
        cand_text_1 = "Alice: Candidate attempt 1."
        cand_text_2 = "Alice: Candidate attempt 2 with different words."

        p1 = build_forensic_judge_user_content("Authoritative governance context", ref_text, cand_text_1)
        p2 = build_forensic_judge_user_content("Authoritative governance context", ref_text, cand_text_2)

        # Both prompts must start with reference transcript block
        self.assertIn("<reference_ground_truth>", p1)
        ref_idx_1 = p1.index("<reference_ground_truth>")
        cand_idx_1 = p1.index("<candidate_output_1>")
        self.assertLess(ref_idx_1, cand_idx_1, "Reference ground truth must precede candidate output for prefix caching")

        # The prefix up to the candidate output must be strictly identical
        prefix_1 = p1[:cand_idx_1]
        prefix_2 = p2[:p2.index("<candidate_output_1>")]
        self.assertEqual(prefix_1, prefix_2, "Prefix prior to candidate output must match 100% across attempts")

    def test_prompt_prefix_cache_invariance_recap(self) -> None:
        """Verify reference transcript strictly precedes candidate in recap judge prompt."""
        from dual_pipeline.llm_judge_core.judges.recap import build_recap_judge_user_content

        ref_text = "Fixed transcript context for recap evaluation."
        c1 = "# Executive Summary\nAttempt 1"
        c2 = "# Executive Summary\nAttempt 2 differs"

        p1 = build_recap_judge_user_content("Authoritative governance context", ref_text, c1)
        p2 = build_recap_judge_user_content("Authoritative governance context", ref_text, c2)

        ref_idx = p1.index("<reference_forensic_transcript>")
        cand_idx = p1.index("<candidate_output_2>")
        self.assertLess(ref_idx, cand_idx, "Reference transcript must precede candidate in recap prompt")

        prefix_1 = p1[:cand_idx]
        prefix_2 = p2[:p2.index("<candidate_output_2>")]
        self.assertEqual(prefix_1, prefix_2, "Recap judge prefix must be invariant across candidate iterations")

    def test_prompt_prefix_cache_invariance_graph(self) -> None:
        """Verify reference transcript strictly precedes candidate in graph judge prompt."""
        from dual_pipeline.llm_judge_core.judges.graph import build_graph_judge_user_content

        ref_text = "Fixed transcript for knowledge graph extraction."
        g1 = '{"entities": [{"id": "person:jen"}]}'
        g2 = '{"entities": [{"id": "person:amit"}]}'

        p1 = build_graph_judge_user_content("Authoritative governance context", ref_text, g1)
        p2 = build_graph_judge_user_content("Authoritative governance context", ref_text, g2)

        ref_idx = p1.index("<reference_forensic_transcript>")
        cand_idx = p1.index("<candidate_output_3_json>")
        self.assertLess(ref_idx, cand_idx, "Reference transcript must precede candidate in graph prompt")

        prefix_1 = p1[:cand_idx]
        prefix_2 = p2[:p2.index("<candidate_output_3_json>")]
        self.assertEqual(prefix_1, prefix_2, "Graph judge prefix must be invariant across candidates")

    def test_token_cap_right_sizing_invariants(self) -> None:
        """Enforce strict upper bounds on max output token limits."""
        self.assertLessEqual(DEFAULT_SYNTHESIS_MAX_TOKENS, 64000, "Synthesis token cap exceeds cost ceiling")
        self.assertLessEqual(DEFAULT_RECAP_MAX_TOKENS, 24000, "Recap token cap exceeds cost ceiling")
        self.assertLessEqual(DEFAULT_FAST_MAX_TOKENS, 32000, "Fast token cap exceeds cost ceiling")

        # Verify Gemini gen_config defaults to 32000
        cfg = _build_gemini_generation_config(None, None, False, None)
        self.assertEqual(cfg["maxOutputTokens"], 32000, "Gemini maxOutputTokens must be right-sized to 32000")

    def test_concordance_edge_case_zero_score(self) -> None:
        """Verify compute_concordance handles 0-score edge cases without ZeroDivisionError."""
        prod = TierEvaluationResult(
            tier="production",
            model="gpt-6-astra",
            effort="max",
            output_1_score=0,
            output_1_verdict="FAIL",
            output_2_score=0,
            output_2_verdict="FAIL",
            output_3_score=0,
            output_3_verdict="FAIL",
            reconciliation_status="FAIL",
            overall_status="FAIL",
            estimated_cost=0.50,
            input_tokens=38000,
            output_tokens=2400,
        )
        cand = TierEvaluationResult(
            tier="fast",
            model="gpt-6-luna",
            effort="low",
            output_1_score=0,
            output_1_verdict="FAIL",
            output_2_score=0,
            output_2_verdict="FAIL",
            output_3_score=0,
            output_3_verdict="FAIL",
            reconciliation_status="FAIL",
            overall_status="FAIL",
            estimated_cost=0.005,
            input_tokens=38000,
            output_tokens=2400,
        )
        res = compute_concordance(prod, cand)
        self.assertTrue(res["verdict_match"])
        self.assertEqual(res["mean_score_concordance_pct"], 100.0)
        self.assertAlmostEqual(res["cost_savings_pct"], 99.0, places=1)

    def test_concordance_cost_savings_monotonicity(self) -> None:
        """Verify cost progression: fast < standard < production."""
        c_prod = _estimate_tier_cost("production")
        c_std = _estimate_tier_cost("standard")
        c_fast = _estimate_tier_cost("fast")

        self.assertLess(c_fast, c_std, "Fast tier cost must be strictly cheaper than standard tier")
        self.assertLess(c_std, c_prod, "Standard tier cost must be strictly cheaper than production tier")
        self.assertGreater(c_prod, 0.20, "Production tier cost must reflect frontier pricing")
        self.assertLess(c_fast, 0.05, "Fast tier cost must reflect sub-cent evaluation efficiency")

    def test_gemini_context_cache_noop_offline(self) -> None:
        """Verify create_gemini_context_cache and delete safely no-op when unconfigured."""
        name = create_gemini_context_cache("gemini-2.5-pro", "test transcript text")
        # Without credentials or in offline environment, returns None safely
        self.assertTrue(name is None or isinstance(name, str))
        # Deletion of None/empty must not raise
        delete_gemini_context_cache("")
        if name:
            delete_gemini_context_cache(name)


if __name__ == "__main__":
    unittest.main()
