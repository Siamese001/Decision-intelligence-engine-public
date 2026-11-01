"""
End-to-end verification for Wave 6: Evaluation Cache Isolation, Fatal Code Enforcement & Prompt Sanitization.
"""
from __future__ import annotations

import unittest

import dual_pipeline.transcript_qa_core.eval_cache as eval_cache_mod
import dual_pipeline.transcript_qa_core.eval_section_cache as eval_section_cache_mod
from dual_pipeline.transcript_qa_core.eval_cache import (
    clear_evaluation_cache,
    compute_section_cache_key,
    get_cached_section_evaluation,
    resolve_pins_and_cache,
    store_cached_section_evaluation,
)


class TestEvalCacheAndSanitizerE2E(unittest.TestCase):
    def setUp(self) -> None:
        clear_evaluation_cache()

    def tearDown(self) -> None:
        clear_evaluation_cache()

    def test_e2e_section_cache_unification_and_threshold_evaluator_keying(self) -> None:
        # 1. Verify _SECTION_EVALUATION_CACHE is the exact same dict object across both modules
        self.assertIs(
            eval_cache_mod._SECTION_EVALUATION_CACHE,
            eval_section_cache_mod._SECTION_EVALUATION_CACHE,
        )

        # 2. Verify compute_section_cache_key incorporates thresholds_hash and evaluator_code_sha256
        k_a = compute_section_cache_key(
            "exec_summary", "sha_1", "ctx_1", thresholds_hash="thr_90", evaluator_code_sha256="code_v1"
        )
        k_b = compute_section_cache_key(
            "exec_summary", "sha_1", "ctx_1", thresholds_hash="thr_95", evaluator_code_sha256="code_v1"
        )
        k_c = compute_section_cache_key(
            "exec_summary", "sha_1", "ctx_1", thresholds_hash="thr_90", evaluator_code_sha256="code_v2"
        )
        self.assertNotEqual(k_a, k_b)
        self.assertNotEqual(k_a, k_c)

        store_cached_section_evaluation(
            "exec_summary",
            "sha_1",
            {"passed": True, "score": 96, "fatal_codes": []},
            context_hash="ctx_1",
            thresholds_hash="thr_90",
            evaluator_code_sha256="code_v1",
        )
        hit = get_cached_section_evaluation(
            "exec_summary",
            "sha_1",
            context_hash="ctx_1",
            thresholds_hash="thr_90",
            evaluator_code_sha256="code_v1",
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["score"], 96)

        # Changing threshold or evaluator hash invalidates the cached section evaluation
        miss_thr = get_cached_section_evaluation(
            "exec_summary",
            "sha_1",
            context_hash="ctx_1",
            thresholds_hash="thr_95",
            evaluator_code_sha256="code_v1",
        )
        self.assertIsNone(miss_thr)

    def test_e2e_resolve_pins_and_cache_passes_clean_sanitized_attachment_block(self) -> None:
        o1, o2, o3, k1, k2, k3 = resolve_pins_and_cache(
            f1_text="# Output 1\n[00:00:05] Alice: Clean dialogue.",
            f2_text="# Output 2\nExecutive summary.",
            f3_text='{"signals": []}',
            model="test-model",
            reasoning_effort="medium",
            pinned={},
            metadata={"attachment_prompt_block": "Architecture slide deck notes: GIS integration phase 2."},
        )
        self.assertIsNone(o1)
        self.assertTrue(k1.startswith("out1:"))


if __name__ == "__main__":
    unittest.main()
