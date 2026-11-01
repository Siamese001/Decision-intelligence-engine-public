"""
tests/test_pipeline_a_high_signal_eval.py

Pipeline A High-Signal Adversarial Evaluation Test Suite.
Validates:
1. Metric A.1 (S_noise): 4-regime DSP boundary resilience (silence, clipping, clean speech, low SNR).
2. Metric A.2 (A_locking): 100% strict locking of ungrounded and placeholder tags to 'speaker_unknown'.
3. Metric A.3 (P_enrolled): 100% preservation of verified roster identities.
4. Metric A.4 (L_chunk): Streaming chunk latency SLA compliance (<500ms).
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.pipeline_a_evaluator import PipelineAEvaluator


class TestPipelineAHighSignalEval(unittest.TestCase):
    def setUp(self):
        self.enrolled = ["Alice Smith", "Drew Oakley", "Elena Rostova"]
        self.evaluator = PipelineAEvaluator(enrolled_speakers=self.enrolled)

    def test_waveform_resilience_4regimes(self):
        """Verify Metric A.1 across all 4 distinct acoustic challenge regimes."""
        res = self.evaluator.evaluate_waveform_resilience()
        self.assertTrue(res["passed"], f"Waveform resilience failed: {res}")
        self.assertGreaterEqual(res["score"], 0.75)
        self.assertEqual(res["regimes_tested"], 6)

        subtests = res["subtests"]
        self.assertTrue(subtests["silence_detected"])
        self.assertTrue(subtests["silence_rms_below_floor"])
        self.assertTrue(subtests["clipping_detected"])
        self.assertTrue(subtests["clean_signal_valid"])
        self.assertTrue(subtests["clean_speech_detected"])
        self.assertTrue(subtests["low_snr_computed"])

    def test_ungrounded_speaker_locking(self):
        """Verify Metric A.2 strictly normalizes ungrounded tokens to speaker_unknown."""
        placeholder_tags = [
            "",
            "   ",
            "Speaker",
            "speaker",
            "Speaker A",
            "SPEAKER B",
            "Speaker 1",
            "Speaker 2",
            "unknown",
            "Attendee",
            "user",
            "Unassigned",
        ]
        res = self.evaluator.evaluate_speaker_locking(placeholder_tags)
        self.assertTrue(res["passed"])
        self.assertEqual(res["accuracy"], 1.0)
        self.assertEqual(res["locked_count"], len(placeholder_tags))
        self.assertEqual(len(res["failures"]), 0)

        # Test strict enrollment mode on arbitrary unregistered guests
        strict_evaluator = PipelineAEvaluator(
            enrolled_speakers=self.enrolled,
            strict_enrollment_only=True,
        )
        unregistered_tags = ["guest_42", "unregistered_participant", "caller_external"]
        res_strict = strict_evaluator.evaluate_speaker_locking(unregistered_tags)
        self.assertTrue(res_strict["passed"])
        self.assertEqual(res_strict["accuracy"], 1.0)
        self.assertEqual(res_strict["locked_count"], len(unregistered_tags))


    def test_enrolled_speaker_preservation(self):
        """Verify Metric A.3 preserves verified enrolled roster identities."""
        res = self.evaluator.evaluate_enrolled_preservation()
        self.assertTrue(res["passed"])
        self.assertEqual(res["preservation_rate"], 1.0)
        self.assertEqual(res["preserved_count"], len(self.enrolled))

    def test_streaming_chunk_latency_sla(self):
        """Verify Metric A.4 maintains chunk processing latency < 500ms SLA."""
        res = self.evaluator.evaluate_chunk_streaming_latency(chunk_count=30)
        self.assertTrue(res["sla_passed"])
        self.assertLess(res["max_latency_ms"], 500.0)
        self.assertLess(res["avg_latency_ms"], 50.0)

    def test_pipeline_a_scorecard_e2e(self):
        """Verify end-to-end PipelineAEvalScorecard meets all high-signal thresholds."""
        scorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.noise_floor_resilience_score, 0.75)
        self.assertEqual(scorecard.speaker_locking_accuracy, 1.0)
        self.assertEqual(scorecard.enrolled_speaker_preservation, 1.0)
        self.assertTrue(scorecard.chunk_latency_sla_passed)
        self.assertIn("PASS", scorecard.summary)


if __name__ == "__main__":
    unittest.main()
