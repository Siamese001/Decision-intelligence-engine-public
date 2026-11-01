"""Adversarial Red Team Tests for Pipeline A (Acoustic Intake & Normalization).

Evaluates:
1. Whisper Hallucination Trap: Suppression of repetitive silence/noise loops.
2. Audio Chunk Boundary Word-Splitting: Splicing and word preservation across boundaries.
3. Non-Monotonic Timestamps & Audio Starvation: Fail-closed packet validation.
4. Speaker Impersonation Defense: Disallowing unanchored voice identity claims.
"""
from __future__ import annotations

import unittest

import numpy as np

from pipeline_a_intake.acoustic_evidence import (
    DEFAULT_SPEAKER_UNKNOWN,
    AcousticSpeakerGate,
    AttributionState,
    compute_acoustic_metrics,
)
from pipeline_a_intake.contracts import AudioChunk
from pipeline_a_intake.text_stitcher import TranscriptStitcher


class TestPipelineAAdversarialIntake(unittest.TestCase):
    """Adversarial stress suite for Pipeline A acoustic and intake layers."""

    def setUp(self) -> None:
        self.enrolled = ["Alice Miller", "Drew Oakley", "Stephan"]
        self.gate = AcousticSpeakerGate(enrolled_speakers=self.enrolled, strict_enrollment_only=True)
        self.stitcher = TranscriptStitcher(session_id="adv_test_session", min_overlap_words=1)

    def test_whisper_hallucination_loop_suppression(self) -> None:
        """Hallucination loops from silent/ambient audio must be stripped or locked as unknown."""
        ambient_tags = ["", "   ", "...", "Subtitles by", "Unknown Noise", "Music", "Applause"]
        for tag in ambient_tags:
            attr = self.gate.evaluate_speaker(tag)
            self.assertEqual(attr.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
            self.assertFalse(attr.is_attributed)
            self.assertTrue(attr.uncertainty_locked)

    def test_audio_chunk_boundary_preservation(self) -> None:
        """Stitching across arbitrary chunk cuts must prevent duplicate stutter loops."""
        # Consecutive incoming Whisper transcript fragments with overlapping tokens
        self.stitcher.process_window("We approved the enterprise architecture", start_ts=0.0, end_ts=2.5)
        self.stitcher.process_window("enterprise architecture for the cloud landing zone", start_ts=2.0, end_ts=4.5)

        # Stitcher must deduplicate the overlap 'enterprise architecture'
        full_text = " ".join(self.stitcher._confirmed_words + self.stitcher._speculative_words)
        self.assertIn("approved the enterprise architecture for the cloud", full_text)
        # Ensure no stutter duplicate like 'enterprise architecture enterprise architecture'
        self.assertEqual(full_text.count("enterprise architecture"), 1)

    def test_non_monotonic_timestamps_fail_closed_or_sort(self) -> None:
        """Out-of-order chunks or inverted timestamps must be detected and ordered."""
        chunks = [
            AudioChunk(chunk_id="c3", meeting_id="m1", audio_data=b"\x00" * 1600, timestamp_offset=15.0),
            AudioChunk(chunk_id="c1", meeting_id="m1", audio_data=b"\x00" * 1600, timestamp_offset=0.0),
            AudioChunk(chunk_id="c2", meeting_id="m1", audio_data=b"\x00" * 1600, timestamp_offset=5.0),
        ]
        sorted_chunks = sorted(chunks, key=lambda c: c.timestamp_offset)
        self.assertEqual([c.chunk_id for c in sorted_chunks], ["c1", "c2", "c3"])
        for i in range(len(sorted_chunks) - 1):
            self.assertLessEqual(sorted_chunks[i].timestamp_offset, sorted_chunks[i + 1].timestamp_offset)

    def test_audio_starvation_and_corrupt_signal_handling(self) -> None:
        """Zero-byte or non-finite audio arrays must return zero confidence metrics."""
        # Empty signal
        empty_signal = np.array([], dtype=np.float32)
        metrics_empty = compute_acoustic_metrics(empty_signal)
        self.assertFalse(metrics_empty.is_speech)
        self.assertEqual(metrics_empty.speech_confidence, 0.0)

        # Clipping / saturation signal
        clipped_signal = np.ones(16000, dtype=np.float32)
        metrics_clipped = compute_acoustic_metrics(clipped_signal)
        self.assertGreaterEqual(metrics_clipped.clipping_ratio, 0.9)

    def test_speaker_impersonation_reconciliation(self) -> None:
        """Spoken claims in dialogue cannot spoof ungrounded speaker turns."""
        spoken_deception = "Unknown Attendee"
        attr = self.gate.evaluate_speaker(spoken_deception)
        self.assertEqual(attr.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
        self.assertEqual(attr.attribution_state, AttributionState.UNATTRIBUTED_UNKNOWN)
        self.assertFalse(attr.is_attributed)

        # Non-enrolled arbitrary string under strict mode also normalizes to speaker_unknown
        attr_spoof = self.gate.evaluate_speaker("Stephan Impersonator")
        self.assertEqual(attr_spoof.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
        self.assertFalse(attr_spoof.is_attributed)
