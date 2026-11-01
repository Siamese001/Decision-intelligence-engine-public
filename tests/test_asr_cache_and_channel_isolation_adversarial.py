"""
tests/test_asr_cache_and_channel_isolation_adversarial.py

Class B Adversarial & Edge-Case Test Suite for Wave 1:
Word-Level Live ASR Cache & Stereo Channel Isolation.

Covers Mandatory Edge-Case Categories:
1. Invariant Bypass Attempt (GAP-ASR-01): Bypassing wordless cached ASR segments
   when word_timestamps=True is required for speaker alignment.
2. Boundary Payload: 0-byte, sub-10ms, and digital-silence audio handling in RMS calculation.
3. Malformed Input: Truncated WAV headers and corrupt audio byte streams.
4. Overflow & Extremes: Extreme channel amplitude ratios (100x Ch1 vs Ch0, 100x Ch0 vs Ch1).
5. Exact Production Regression: Intra-segment continuity smoothing prevents micro-pauses
   from collapsing to 'Unknown', while strictly refusing to bridge gaps > 0.25s.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.asr_decode_config import ASRDecodeConfig, DEFAULT_ASR_DECODE_CONFIG
from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
    load_audio_asr_cache,
    save_audio_asr_cache,
)
from dual_pipeline.audio_ingestion_core.continuity_smoother import (
    group_words_into_subsegments,
    smooth_intra_segment_continuity,
)
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.audio_ingestion_core.speaker_alignment import AcousticInterval
from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
    compute_channel_rms,
    compute_channel_rms_ratio,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry


class TestASRCacheAndChannelIsolationAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wave1_adv_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        for p in Path("reports").glob("*sample*acoustic_provenance_receipt.json"):
            try:
                p.unlink()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Category 1: Invariant Bypass Attempt (GAP-ASR-01 Regression)
    # -------------------------------------------------------------------------
    def test_wordless_cache_bypass_when_word_timestamps_required(self):
        """Bypasses pre-existing cached ASR segments if they lack 'words' array."""
        cache_dir = self.temp_path / "asr_cache"
        model_name = "mlx-community/whisper-large-v3-turbo"

        dummy_audio = self.temp_path / "sample.wav"
        with wave.open(str(dummy_audio), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 32000)

        audio_sha = hashlib.sha256(dummy_audio.read_bytes()).hexdigest()[:16]

        # Save legacy wordless cache
        wordless_segments = [
            {"start": 0.0, "end": 5.0, "text": "This segment has no word timestamps."}
        ]
        save_audio_asr_cache(
            audio_sha256=audio_sha,
            raw_segments=wordless_segments,
            model_name=model_name,
            cache_dir=cache_dir,
            stem=dummy_audio.stem,
            mock=False,
        )

        # Transcriber configured with word_timestamps=True must detect missing words and bypass
        transcriber = LocalMLXAudioTranscriber(model_name=model_name, mock=False)

        with patch("mlx_whisper.transcribe") as mock_transcribe, \
             patch("dual_pipeline.memory_guard.assert_memory_headroom"), \
             patch.object(transcriber, "_diarize_and_align", return_value=[{"speaker": "SPEAKER_00", "text": "This segment has words.", "start": 0.0, "end": 5.0}]):

            fresh_segments = [
                {
                    "start": 0.0,
                    "end": 5.0,
                    "text": "This segment has words.",
                    "words": [{"word": "This", "start": 0.0, "end": 0.5}],
                }
            ]
            mock_transcribe.return_value = {
                "text": "This segment has words.",
                "segments": fresh_segments,
            }

            res = transcriber.transcribe(
                audio_file_path=str(dummy_audio),
                metadata={"transcript_cache_dir": str(cache_dir)},
            )
            # Fresh transcription MUST have been called because wordless cache was bypassed
            mock_transcribe.assert_called_once()
            self.assertIsNotNone(res)

    # -------------------------------------------------------------------------
    # Category 2: Boundary Payload
    # -------------------------------------------------------------------------
    def test_boundary_payload_zero_byte_and_digital_silence(self):
        """Zero-byte file or digital silence handles RMS calculation without div-by-zero."""
        empty_wav = self.temp_path / "empty.wav"
        empty_wav.write_bytes(b"")

        ratio = compute_channel_rms_ratio(empty_wav)
        self.assertEqual(ratio, (0.0, 0.0))

        silence_wav = self.temp_path / "silence.wav"
        with wave.open(str(silence_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 6400)

        ch0_rms, ch1_rms = compute_channel_rms_ratio(silence_wav)
        self.assertEqual(ch0_rms, 0.0)
        self.assertEqual(ch1_rms, 0.0)

    # -------------------------------------------------------------------------
    # Category 3: Malformed Input
    # -------------------------------------------------------------------------
    def test_malformed_wav_file_fails_safe(self):
        """Corrupt audio payload returns safe fallback without unhandled exception."""
        corrupt_wav = self.temp_path / "corrupt.wav"
        corrupt_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00TRUNCATED")

        res = compute_channel_rms_ratio(corrupt_wav)
        self.assertEqual(res, (0.0, 0.0))

        nonexistent = self.temp_path / "does_not_exist.wav"
        res_nonexistent = compute_channel_rms_ratio(nonexistent)
        self.assertEqual(res_nonexistent, (0.0, 0.0))

    # -------------------------------------------------------------------------
    # Category 4: Overflow & Extreme Values
    # -------------------------------------------------------------------------
    def test_extreme_channel_dominance_ratios(self):
        """Extreme channel dominance ratios consistently route to Host vs Remote."""
        registry = LiveVoiceRegistry(similarity_threshold=0.78)

        # 100x Channel 1 dominance (Local Mic)
        ch1_heavy_wav = self.temp_path / "ch1_heavy.wav"
        with wave.open(str(ch1_heavy_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            for i in range(16000):
                val0 = 20 if (i % 32 < 16) else -20
                val1 = 2000 if (i % 32 < 16) else -2000
                frames.extend(struct.pack("<hh", val0, val1))
            wf.writeframes(frames)

        spk1, vid1, conf1 = registry.resolve_chunk_speaker(
            wav_path=ch1_heavy_wav,
            chunk_idx=0,
            text="Host speech speaking directly into microphone.",
            start_sec=0.0,
            duration_sec=1.0,
        )
        self.assertEqual(spk1, "Host")
        self.assertEqual(vid1, "Voice Host")

        # 100x Channel 0 dominance (Remote SCK)
        ch0_heavy_wav = self.temp_path / "ch0_heavy.wav"
        with wave.open(str(ch0_heavy_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            for i in range(16000):
                val0 = 2000 if (i % 32 < 16) else -2000
                val1 = 20 if (i % 32 < 16) else -20
                frames.extend(struct.pack("<hh", val0, val1))
            wf.writeframes(frames)

        spk2, vid2, conf2 = registry.resolve_chunk_speaker(
            wav_path=ch0_heavy_wav,
            chunk_idx=1,
            text="Remote participant speech from call.",
            start_sec=1.0,
            duration_sec=1.0,
        )
        # Remote participant should NOT route to Voice Host
        self.assertNotEqual(vid2, "Voice Host")
        self.assertTrue(vid2.startswith("Voice "))

    # -------------------------------------------------------------------------
    # Category 5: Exact Production Regression & Continuity Boundaries
    # -------------------------------------------------------------------------
    def test_continuity_smoother_respects_temporal_gap_limits(self):
        """Continuity smoother bridges <= 0.25s gaps but strictly refuses > 0.25s gaps."""
        seg = {"start": 0.0, "end": 10.0, "text": "Word one. Word two."}

        # Case A: Gap <= 0.25s between known speaker words -> should bridge
        words_close = [
            {"word": "Hello", "start": 1.0, "end": 1.5, "speaker": "SPEAKER_01"},
            {"word": "there", "start": 1.7, "end": 2.0, "speaker": "Unknown"},  # Gap 0.2s from 1.5
            {"word": "friend", "start": 3.0, "end": 3.5, "speaker": "SPEAKER_02"},
        ]
        smooth_intra_segment_continuity(
            words_close,
            seg,
            intervals=[],
            fallback_aligner=lambda s, invs: {"speaker": "Unknown"},
        )
        self.assertEqual(words_close[1]["speaker"], "SPEAKER_01")

        # Case B: Gap > 0.25s between different utterances -> must NOT bridge
        words_far = [
            {"word": "Hello", "start": 1.0, "end": 1.5, "speaker": "SPEAKER_01"},
            {"word": "anyone", "start": 2.2, "end": 2.5, "speaker": "Unknown"},  # Gap 0.7s from 1.5
            {"word": "friend", "start": 3.0, "end": 3.5, "speaker": "SPEAKER_02"},
        ]
        smooth_intra_segment_continuity(
            words_far,
            seg,
            intervals=[],
            fallback_aligner=lambda s, invs: {"speaker": "Unknown"},
        )
        self.assertEqual(words_far[1]["speaker"], "Unknown")


if __name__ == "__main__":
    unittest.main()
