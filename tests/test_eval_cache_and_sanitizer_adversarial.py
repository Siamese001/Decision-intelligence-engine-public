"""
Adversarial and edge-case tests for Wave 6: Evaluation Cache Isolation, Fatal Code Enforcement & Prompt Sanitization.
Covers:
1. Invariant bypass attempt (scorecard or section evaluation with score >= 90 but non-empty fatal_codes rejected from cache and pins)
2. Pre-egress security violation (attachment_prompt_block containing INTERNAL_ADVISORY, career leverage, or secret token raises EgressSanitizationError)
3. Exact production regression (clear_evaluation_cache clears the unified _SECTION_EVALUATION_CACHE without shadow dict leak)
"""
from __future__ import annotations

import hashlib
import unittest

import dual_pipeline.transcript_qa_core.eval_cache as eval_cache_mod
from dual_pipeline.sanitizer import EgressSanitizationError
from dual_pipeline.transcript_qa_core.eval_cache import (
    check_pin,
    clear_evaluation_cache,
    get_cached_section_evaluation,
    is_borderline_or_hitl,
    resolve_pins_and_cache,
    store_cached_section_evaluation,
)


class TestEvalCacheAndSanitizerAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        clear_evaluation_cache()

    def tearDown(self) -> None:
        clear_evaluation_cache()

    def test_adversarial_fatal_codes_never_cached_or_accepted_even_when_score_above_90(self) -> None:
        # Category: Invariant bypass attempt
        ev_with_fatal = {
            "passed": True,
            "verdict": "PASS",
            "composite_score": 96,
            "fatal_codes": ["FATAL_HALLUCINATED_SPEAKER"],
        }
        self.assertTrue(is_borderline_or_hitl(ev_with_fatal))

        store_cached_section_evaluation("sec_1", "hash_1", ev_with_fatal)
        self.assertIsNone(get_cached_section_evaluation("sec_1", "hash_1"))

        cand_text = "Sample output text"
        cand_sha = hashlib.sha256(cand_text.encode("utf-8")).hexdigest()
        pinned = {
            "output_1": {
                "artifact_sha256": cand_sha,
                "composite_score": 95,
                "verdict": "PASS",
                "fatal_codes": ["FATAL_C0_MISMATCH"],
            }
        }
        self.assertIsNone(check_pin(pinned, "output_1", "output_01", 90, cand_sha))

    def test_adversarial_attachment_prompt_block_scrubbed_by_pre_egress_sanitizer(self) -> None:
        # Category: Invariant bypass / Egress sanitization
        for forbidden_payload in (
            "Confidential note: INTERNAL_ADVISORY power map for promotion",
            "Secret token sk_test_redacted_placeholder leaked in attachment",
            "Consultant rate is $250/hr for this engagement",
        ):
            with self.assertRaises(EgressSanitizationError):
                resolve_pins_and_cache(
                    f1_text="Clean transcript",
                    f2_text="Clean recap",
                    f3_text="{}",
                    model="test-model",
                    reasoning_effort="medium",
                    pinned={},
                    metadata={"attachment_prompt_block": forbidden_payload},
                )

    def test_adversarial_clear_evaluation_cache_leaves_zero_shadow_entries(self) -> None:
        # Category: Exact production regression
        store_cached_section_evaluation("sec_clean", "hash_clean", {"passed": True, "score": 95})
        self.assertEqual(len(eval_cache_mod._SECTION_EVALUATION_CACHE), 1)
        clear_evaluation_cache()
        self.assertEqual(len(eval_cache_mod._SECTION_EVALUATION_CACHE), 0)


if __name__ == "__main__":
    unittest.main()
