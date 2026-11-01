#!/usr/bin/env python3
"""
tests/test_judge_model_binding.py

Unit test suite verifying gpt-5.6-luna model binding, SSOT credential resolution,
and execution across DecomposedMultiJudge, LLMTranscriptSynthesizer, and SpeakerDisambiguator.
"""

import os
import unittest
from unittest import mock

from config.credentials import ensure_ssot_env_loaded
from dual_pipeline.credentials import (
    GOVERNED_MODELS,
    resolve_openai_credentials,
)
from dual_pipeline.llm_judge import (
    DecomposedMultiJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
)
from dual_pipeline.speaker_disambiguation import SpeakerDisambiguator
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.transcript_stages import LLMTranscriptSynthesizer

ensure_ssot_env_loaded()


class TestJudgeModelBinding(unittest.TestCase):
    def test_governed_models_contains_gpt56_luna(self):
        self.assertIn("gpt-5.6-luna", GOVERNED_MODELS)

    def test_decomposed_multi_judge_gpt56_luna_propagation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "high")
        self.assertEqual(judge.judge_forensic.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_recap.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_graph.model, "gpt-5.6-luna")

    def test_decomposed_multi_judge_gpt56_luna_mock_evaluation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        f1_text = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on system integration.\n"
        f2_text = "# Executive Recap\n\n**Date:** 08/28/2026\n\n## Action Items\n- Alex Rivera to review architecture."
        res = judge.evaluate_parallel(
            output_1_text=f1_text,
            output_2_text=f2_text,
            output_3_text='{"signals": []}',
            raw_transcript=f1_text,
            metadata={"date": "08/28/2026", "meeting_name": "Project Atlas"},
        )
        self.assertEqual(res["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "high")
        self.assertIn(res["status"], ("PASS", "PASS_ADVISORY", "FAIL"))
        self.assertIn("output_1_evaluation", res)
        self.assertIn("output_2_evaluation", res)
        self.assertIn("output_3_evaluation", res)

    def test_synthesizer_gpt56_luna_credential_resolution(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(ValueError) as ctx:
                LLMTranscriptSynthesizer(model="gpt-5.6-luna")
            self.assertIn("OPENAI_API_KEY is required", str(ctx.exception))

    def test_evaluate_judge_and_release_with_gpt56_luna(self):
        f1 = "### [00:00:00]\n\n**Speaker 1:** Hello team."
        f2 = "# Recap\n\nDate: 08/28/2026"
        f3 = '{"signals": []}'
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1,
            f2_text=f2,
            f3_text=f3,
            source_text=f1,
            mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(judge_rep["evaluator_model"], "gpt-5.6-luna")
        self.assertIn("authoritative_write_authorized", decision)

    def test_speaker_disambiguator_with_gpt56_luna(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            disambiguator = SpeakerDisambiguator(
                model="gpt-5.6-luna",
                reasoning_effort="high",
                mock=False,
            )
            # Should fall back to mock=True safely when key is absent
            client = disambiguator.client
            self.assertIsNone(client)
            self.assertTrue(disambiguator.mock)


    def test_decomposed_multi_judge_gpt56_sol_medium_defaults(self):
        judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="medium", mock=True)
        self.assertEqual(judge.model, "gpt-5.6-sol")
        self.assertEqual(judge.reasoning_effort, "medium")
        self.assertEqual(judge.judge_forensic.model, "gpt-5.6-sol")
        self.assertEqual(judge.judge_forensic.reasoning_effort, "medium")
        self.assertEqual(judge.judge_recap.model, "gpt-5.6-sol")
        self.assertEqual(judge.judge_recap.reasoning_effort, "medium")
        self.assertEqual(judge.judge_graph.model, "gpt-5.6-sol")
        self.assertEqual(judge.judge_graph.reasoning_effort, "medium")

    def test_decomposed_multi_judge_default_model_luna_medium(self):
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "gpt-5.6-luna", "OPENAI_REASONING_EFFORT": "medium"}):
            judge = DecomposedMultiJudge(mock=True)
            self.assertEqual(judge.model, "gpt-5.6-luna")
            self.assertEqual(judge.reasoning_effort, "medium")
            self.assertEqual(judge.judge_forensic.model, "gpt-5.6-luna")
            self.assertEqual(judge.judge_forensic.reasoning_effort, "medium")
            self.assertEqual(judge.judge_recap.model, "gpt-5.6-luna")
            self.assertEqual(judge.judge_recap.reasoning_effort, "medium")
            self.assertEqual(judge.judge_graph.model, "gpt-5.6-luna")
            self.assertEqual(judge.judge_graph.reasoning_effort, "medium")

    def test_micro_judges_sol_medium_defaults(self):
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        target_m, target_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        j1 = ForensicTranscriptJudge()
        j2 = ExecutiveRecapJudge()
        j3 = GraphExtractionJudge()
        self.assertEqual(j1.model, target_m)
        self.assertEqual(j1.reasoning_effort, target_e)
        self.assertEqual(j2.model, target_m)
        self.assertEqual(j2.reasoning_effort, target_e)
        self.assertEqual(j3.model, target_m)
        self.assertEqual(j3.reasoning_effort, target_e)


    def test_governed_models_contains_gpt56_luna(self):
        self.assertIn("gpt-5.6-luna", GOVERNED_MODELS)

    def test_decomposed_multi_judge_gpt56_luna_high_propagation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "high")
        self.assertEqual(judge.judge_forensic.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_forensic.reasoning_effort, "high")
        self.assertEqual(judge.judge_recap.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_recap.reasoning_effort, "high")
        self.assertEqual(judge.judge_graph.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_graph.reasoning_effort, "high")

    def test_decomposed_multi_judge_gpt56_luna_high_mock_evaluation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        f1_text = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on system integration.\n"
        f2_text = "# Executive Recap\n\n**Date:** 08/28/2026\n\n## Action Items\n- Alex Rivera to review architecture."
        res = judge.evaluate_parallel(
            output_1_text=f1_text,
            output_2_text=f2_text,
            output_3_text='{"signals": []}',
            raw_transcript=f1_text,
            metadata={"date": "08/28/2026", "meeting_name": "Project Atlas"},
        )
        self.assertEqual(res["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "high")
        self.assertIn(res["status"], ("PASS", "PASS_ADVISORY", "FAIL"))

    def test_evaluate_judge_and_release_with_gpt56_luna_high(self):
        f1 = "### [00:00:00]\n\n**Speaker 1:** Hello team."
        f2 = "# Recap\n\nDate: 08/28/2026"
        f3 = '{"signals": []}'
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1,
            f2_text=f2,
            f3_text=f3,
            source_text=f1,
            mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(judge_rep["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(judge_rep["reasoning_effort"], "high")
        self.assertIn("authoritative_write_authorized", decision)

    def test_speaker_disambiguator_with_gpt56_luna_high(self):
        disambiguator = SpeakerDisambiguator(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        self.assertEqual(disambiguator.model, "gpt-5.6-luna")
        self.assertEqual(disambiguator.reasoning_effort, "high")
        self.assertEqual(disambiguator.margin_threshold, 0.25)

    def test_ssot_credentials_resolution_with_env_di_live_key(self):
        creds = resolve_openai_credentials(
            allow_mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(creds.model, "gpt-5.6-luna")
        self.assertEqual(creds.reasoning_effort, "high")
        self.assertTrue(creds.has_live_key)
        self.assertTrue(creds.masked_key.startswith("sk-proj-"))

    def test_decomposed_multi_judge_gpt56_luna_medium_propagation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            mock=True,
        )
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "medium")
        self.assertEqual(judge.judge_forensic.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_forensic.reasoning_effort, "medium")
        self.assertEqual(judge.judge_recap.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_recap.reasoning_effort, "medium")
        self.assertEqual(judge.judge_graph.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_graph.reasoning_effort, "medium")

    def test_decomposed_multi_judge_gpt56_luna_medium_mock_evaluation(self):
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            mock=True,
        )
        f1_text = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on system integration.\n"
        f2_text = "# Executive Recap\n\n**Date:** 08/28/2026\n\n## Action Items\n- Alex Rivera to review architecture."
        res = judge.evaluate_parallel(
            output_1_text=f1_text,
            output_2_text=f2_text,
            output_3_text='{"signals": []}',
            raw_transcript=f1_text,
            metadata={"date": "08/28/2026", "meeting_name": "Project Atlas"},
        )
        self.assertEqual(res["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "medium")
        self.assertIn(res["status"], ("PASS", "PASS_ADVISORY", "FAIL"))

    def test_evaluate_judge_and_release_with_gpt56_luna_medium(self):
        f1 = "### [00:00:00]\n\n**Speaker 1:** Hello team."
        f2 = "# Recap\n\nDate: 08/28/2026"
        f3 = '{"signals": []}'
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1,
            f2_text=f2,
            f3_text=f3,
            source_text=f1,
            mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="medium",
        )
        self.assertEqual(judge_rep["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(judge_rep["reasoning_effort"], "medium")
        self.assertIn("authoritative_write_authorized", decision)

    def test_speaker_disambiguator_with_gpt56_luna_medium(self):
        disambiguator = SpeakerDisambiguator(
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            mock=True,
        )
        self.assertEqual(disambiguator.model, "gpt-5.6-luna")
        self.assertEqual(disambiguator.reasoning_effort, "medium")
        self.assertEqual(disambiguator.margin_threshold, 0.25)

    def test_evaluate_judge_and_release_with_gpt56_luna_xhigh(self):
        f1 = "### [00:00:00]\n\n**Speaker 1:** Hello team."
        f2 = "# Recap\n\nDate: 08/28/2026"
        f3 = '{"signals": []}'
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1,
            f2_text=f2,
            f3_text=f3,
            source_text=f1,
            mock=True,
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
        )
        self.assertEqual(judge_rep["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(judge_rep["reasoning_effort"], "xhigh")
        self.assertIn("authoritative_write_authorized", decision)

    def test_speaker_disambiguator_with_gpt56_luna_xhigh_20_percent_margin(self):
        disambiguator = SpeakerDisambiguator(
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
            confidence_margin_threshold=0.20,
            mock=True,
        )
        self.assertEqual(disambiguator.model, "gpt-5.6-luna")
        self.assertEqual(disambiguator.reasoning_effort, "xhigh")
        self.assertEqual(disambiguator.margin_threshold, 0.20)


if __name__ == "__main__":
    unittest.main()
