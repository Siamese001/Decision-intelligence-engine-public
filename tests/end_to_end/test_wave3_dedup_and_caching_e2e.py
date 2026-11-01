"""tests/end_to_end/test_wave3_dedup_and_caching_e2e.py

End-to-end test suite for Wave 3 differential judge caching, prompt version content-addressing,
and idempotency verification.
Verifies that:
1. Unchanged artifacts across attempts reuse passing evaluations without re-calling the LLM judge.
2. Judge prompt hash is bound into evaluation cache keys, invalidating cache on prompt/rubric updates.
3. Full idempotency lifecycle with prompt template hash prevents redundant pipeline execution.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_pipeline_core.caching import (
    check_idempotent_meeting_run,
    compute_prompt_template_hash,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    clear_evaluation_cache,
    resolve_pins_and_cache,
    update_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.evaluator import _run_decomposed_judges


class Wave3DedupAndCachingE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        clear_evaluation_cache()

    def tearDown(self) -> None:
        clear_evaluation_cache()

    def test_differential_judge_evaluation_caching_e2e(self) -> None:
        """When outputs 1 and 2 are unchanged on retry, their judge evaluations are pulled from cache."""
        f1_text = "Speaker 1 [00:00:10]: Project status update.\nSpeaker 2 [00:00:20]: On track."
        f2_text = "# Executive Recap\n## Overview\nProject is on track.\n# Appendix: Detailed Stakeholder Perspectives\nStakeholder details."
        f3_attempt1 = json.dumps({"schema_version": "1.0.0", "entities": [{"id": "system:core"}]})
        f3_attempt2 = json.dumps({"schema_version": "1.0.0", "entities": [{"id": "system:core"}, {"id": "org:partner_advisory"}]})

        model = "gpt-6-astra"
        effort = "high"

        # Attempt 1: All 3 evaluations pass and are cached
        out1_eval_pass = {"status": "PASS", "composite_score": 95, "verdict": "PASS"}
        out2_eval_pass = {"status": "PASS", "composite_score": 92, "verdict": "PASS"}
        out3_eval_attempt1 = {"status": "FAIL", "composite_score": 75, "verdict": "FAIL"}

        out1, out2, out3, k1, k2, k3 = resolve_pins_and_cache(
            f1_text, f2_text, f3_attempt1, model, effort, pinned={}
        )
        self.assertIsNone(out1)
        self.assertIsNone(out2)
        self.assertIsNone(out3)

        # Cache attempt 1 results (passing out1 and out2 cached, failing out3 not cached)
        update_evaluation_cache(k1, out1_eval_pass, k2, out2_eval_pass, k3, out3_eval_attempt1)

        # Attempt 2: f1 and f2 are identical, f3 has changed
        out1_cached, out2_cached, out3_new, k1_2, k2_2, k3_2 = resolve_pins_and_cache(
            f1_text, f2_text, f3_attempt2, model, effort, pinned={}
        )
        self.assertIsNotNone(out1_cached)
        self.assertEqual(out1_cached["composite_score"], 95)
        self.assertEqual(out1_cached["source"], "CONTENT_CACHE")

        self.assertIsNotNone(out2_cached)
        self.assertEqual(out2_cached["composite_score"], 92)
        self.assertEqual(out2_cached["source"], "CONTENT_CACHE")

        self.assertIsNone(out3_new)  # New f3 has never been evaluated

        # Verify that _run_decomposed_judges dispatches ONLY out3 to the judge
        mock_judge = MagicMock()
        mock_out3_eval = {"status": "PASS", "composite_score": 96, "verdict": "PASS"}
        mock_judge.judge_graph.evaluate.return_value = mock_out3_eval
        mock_judge.reconciler.reconcile.return_value = {"passed": True}

        res1, res2, res3, recon = _run_decomposed_judges(
            judge=mock_judge,
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_attempt2,
            source_text=f1_text,
            client=MagicMock(),
            mock=False,
            meta={},
            evals=(out1_cached, out2_cached, out3_new),
            keys=(k1_2, k2_2, k3_2),
        )

        # Forensic (out1) and Recap (out2) judges must NOT have been called!
        self.assertEqual(mock_judge.judge_forensic.evaluate.call_count, 0)
        self.assertEqual(mock_judge.judge_recap.evaluate.call_count, 0)
        # Graph (out3) judge MUST have been called exactly once
        self.assertEqual(mock_judge.judge_graph.evaluate.call_count, 1)

        self.assertEqual(res1["composite_score"], 95)
        self.assertEqual(res2["composite_score"], 92)
        self.assertEqual(res3["composite_score"], 96)

    def test_judge_prompt_hash_in_cache_key(self) -> None:
        """Modifying the judge prompt hash must yield a different cache key and invalidate cached scores."""
        f1_text = "Speaker: Notes"
        model = "gpt-6-astra"
        effort = "high"

        with patch("dual_pipeline.transcript_qa_core.eval_cache.get_judge_prompt_hash") as mock_hash:
            mock_hash.return_value = "prompt_hash_v1"
            _, _, _, k1_v1, _, _ = resolve_pins_and_cache(f1_text, "f2", "f3", model, effort, {})
            self.assertIn("prompt_hash_v1", k1_v1)

            # Store passing evaluation under v1
            update_evaluation_cache(k1_v1, {"verdict": "PASS", "composite_score": 94}, "k2", None, "k3", None)

            # Look up with same v1 prompt hash -> Hit!
            out1_v1, _, _, _, _, _ = resolve_pins_and_cache(f1_text, "f2", "f3", model, effort, {})
            self.assertIsNotNone(out1_v1)

            # Look up with updated v2 prompt hash -> Miss!
            mock_hash.return_value = "prompt_hash_v2"
            out1_v2, _, _, k1_v2, _, _ = resolve_pins_and_cache(f1_text, "f2", "f3", model, effort, {})
            self.assertIsNone(out1_v2)
            self.assertIn("prompt_hash_v2", k1_v2)

    def test_idempotency_gate_e2e_with_prompt_hash(self) -> None:
        """Full idempotency lifecycle validates input and prompt hashes together."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_p = Path(tmp_dir)
            base = "EngReview_10-01-2026_1000"
            (out_p / f"01_{base}_hitl_resolved_transcript.md").write_text("transcript text", encoding="utf-8")
            (out_p / f"02_{base}_executive_recap.md").write_text("recap text", encoding="utf-8")
            (out_p / f"03_{base}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")

            prompt_hash = compute_prompt_template_hash()

            decision_payload = {
                "status": "PASS",
                "input_hash": "input_digest_12345",
                "prompt_template_hash": prompt_hash,
            }
            (out_p / "final_release_decision.json").write_text(json.dumps(decision_payload), encoding="utf-8")

            meta = {
                "meeting_name": "EngReview",
                "date_filename": "10-01-2026",
                "time_filename": "1000",
            }

            paths = check_idempotent_meeting_run(
                output_dir=out_p,
                metadata=meta,
                source_file_hash="input_digest_12345",
            )

            self.assertIsNotNone(paths)
            self.assertEqual(paths["02_recap"], str(out_p / f"02_{base}_executive_recap.md"))


if __name__ == "__main__":
    unittest.main()
