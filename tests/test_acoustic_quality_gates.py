"""
Unit tests for Wave 2: Shared Acoustic Quality Engine & Dead Code Pruning.
Verifies FastAcousticQualityGate, HallucinationFilter, TranscriptSanitizer,
and asserts absence of dead transcribe_stream_window method.
"""
from __future__ import annotations

import time
import unittest

import numpy as np

from core.audio.acoustic_quality import (
    ASR_HALLUCINATION_BLOCKLIST,
    FastAcousticQualityGate,
    HallucinationFilter,
    TranscriptSanitizer,
)
from dual_pipeline.audio_ingestion import LocalMLXAudioTranscriber


class AcousticQualityGatesTests(unittest.TestCase):
    def test_fast_acoustic_quality_gate_silence_and_speech(self) -> None:
        gate = FastAcousticQualityGate(sample_rate=16000, silence_threshold_dbfs=-42.0)

        # 1. Total silence (zeros)
        silence_pcm = b"\x00\x00" * 4000  # 250ms
        res_silence = gate.evaluate_pcm(silence_pcm)
        self.assertFalse(res_silence["is_speech"])
        self.assertEqual(res_silence["clipping_ratio"], 0.0)
        self.assertEqual(res_silence["rms_dbfs"], -96.0)

        # 2. Synthetic speech-like sine wave (1000 Hz at moderate volume)
        t = np.linspace(0, 0.25, 4000, endpoint=False)
        tone = (np.sin(2 * np.pi * 1000 * t) * 15000).astype(np.int16)
        speech_pcm = tone.tobytes()
        res_speech = gate.evaluate_pcm(speech_pcm)
        self.assertTrue(res_speech["is_speech"])
        self.assertGreater(res_speech["rms_dbfs"], -20.0)
        self.assertFalse(res_speech["high_clipping_warning"])

        # 3. Clipped audio (maxed out int16 values)
        clipped_tone = np.full(4000, 32767, dtype=np.int16)
        res_clipped = gate.evaluate_pcm(clipped_tone.tobytes())
        self.assertTrue(res_clipped["high_clipping_warning"])
        self.assertGreater(res_clipped["clipping_ratio"], 0.9)

        # 4. Latency performance: 100 evaluations must complete in < 20ms
        t0 = time.perf_counter()
        for _ in range(100):
            gate.evaluate_pcm(speech_pcm)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.02, f"Quality gate took {elapsed*1000:.2f}ms for 100 frames")

    def test_hallucination_filter_ssot(self) -> None:
        for phrase in ASR_HALLUCINATION_BLOCKLIST:
            text = f"Well, {phrase} and see you."
            self.assertTrue(
                HallucinationFilter.is_hallucination(text),
                f"Phrase '{phrase}' should be recognized as hallucination"
            )
            self.assertIsNone(HallucinationFilter.sanitize(text))

        valid_dialogue = "The decision intelligence engine deployment is confirmed."
        self.assertFalse(HallucinationFilter.is_hallucination(valid_dialogue))
        self.assertEqual(HallucinationFilter.sanitize(valid_dialogue), valid_dialogue)

    def test_transcript_sanitizer(self) -> None:
        # Prompt echo stripping
        prompt = "Project Nexus discussion on architecture"
        raw_text = "Project Nexus discussion on architecture We decided to approve the $2M budget."
        clean = TranscriptSanitizer.sanitize(raw_text, whisper_prompt=prompt)
        self.assertEqual(clean, "We decided to approve the $2M budget.")

        # Stutter / Repetition collapsing
        stutter = "We need to finalize the the the contract immediately."
        clean_stutter = TranscriptSanitizer.sanitize(stutter)
        self.assertEqual(clean_stutter, "We need to finalize the contract immediately.")

        # Hallucination returns empty string
        halluc = "Thank you for watching! Please subscribe."
        self.assertEqual(TranscriptSanitizer.sanitize(halluc), "")

    def test_pruned_dead_code_invariant(self) -> None:
        # Architectural purity: transcribe_stream_window must NOT exist on LocalMLXAudioTranscriber
        self.assertFalse(
            hasattr(LocalMLXAudioTranscriber, "transcribe_stream_window"),
            "transcribe_stream_window should be pruned from LocalMLXAudioTranscriber"
        )


if __name__ == "__main__":
    unittest.main()
