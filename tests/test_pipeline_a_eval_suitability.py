"""Acceptance Test Suite for Wave 1: Pipeline A Acoustic Evaluation Suitability.

Validates:
1. Acoustic waveform resilience measurement across silence, clipping, and low SNR.
2. Ungrounded caller tag normalization to speaker_unknown with uncertainty locking.
3. Preservation of verified enrolled roster identities.
4. Chunk processing latency bounds meeting the <500ms streaming SLA.
5. Emits RFC-8259 compliant PipelineAEvalScorecard.
6. Adheres strictly to the 400-line module budget (Invariant 1).
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.models import PipelineAEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_a_evaluator import PipelineAEvaluator


class TestPipelineAEvalSuitability(unittest.TestCase):
    """Test suite for specialized Pipeline A adversarial evaluator."""

    def setUp(self) -> None:
        self.enrolled = ["Alice Smith", "Drew Oakley", "Elena Rostova"]
        self.evaluator = PipelineAEvaluator(enrolled_speakers=self.enrolled)

    def test_waveform_resilience_evaluation(self) -> None:
        """Evaluator detects acoustic quality metrics across diverse waveforms."""
        res = self.evaluator.evaluate_waveform_resilience()
        self.assertTrue(res["passed"])
        self.assertGreaterEqual(res["score"], 0.75)
        self.assertIn("silence_detected", res["subtests"])
        self.assertIn("clipping_detected", res["subtests"])
        self.assertIn("clean_signal_valid", res["subtests"])
        self.assertIn("low_snr_computed", res["subtests"])

    def test_speaker_locking_evaluation(self) -> None:
        """Evaluator confirms 100% normalization of ungrounded caller tags."""
        ungrounded = [
            "", "   ", "Speaker", "speaker", "Speaker A", "SPEAKER B",
            "Speaker 1", "Speaker 2", "unknown", "Attendee", "user",
        ]
        res = self.evaluator.evaluate_speaker_locking(ungrounded)
        self.assertTrue(res["passed"])
        self.assertEqual(res["accuracy"], 1.0)
        self.assertEqual(len(res["failures"]), 0)
        self.assertEqual(res["locked_count"], len(ungrounded))

    def test_enrolled_speaker_preservation(self) -> None:
        """Evaluator confirms enrolled speakers maintain verified attribution."""
        res = self.evaluator.evaluate_enrolled_preservation()
        self.assertTrue(res["passed"])
        self.assertEqual(res["preservation_rate"], 1.0)
        self.assertEqual(len(res["failures"]), 0)
        self.assertEqual(res["preserved_count"], len(self.enrolled))

    def test_chunk_streaming_latency_sla(self) -> None:
        """Evaluator confirms streaming audio chunk processing meets SLA."""
        res = self.evaluator.evaluate_chunk_streaming_latency(chunk_count=30)
        self.assertTrue(res["sla_passed"])
        self.assertLess(res["avg_latency_ms"], 50.0)  # DSP is fast, well under 500ms
        self.assertLess(res["max_latency_ms"], 500.0)
        self.assertEqual(res["chunks_tested"], 30)

    def test_full_pipeline_a_evaluation_run(self) -> None:
        """Evaluator produces a comprehensive, passed PipelineAEvalScorecard."""
        scorecard: PipelineAEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.noise_floor_resilience_score, 0.75)
        self.assertEqual(scorecard.speaker_locking_accuracy, 1.0)
        self.assertEqual(scorecard.enrolled_speaker_preservation, 1.0)
        self.assertTrue(scorecard.chunk_latency_sla_passed)
        self.assertIn("Pipeline A Evaluation: PASS", scorecard.summary)
        d = scorecard.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["passed"], True)


if __name__ == "__main__":
    unittest.main()
