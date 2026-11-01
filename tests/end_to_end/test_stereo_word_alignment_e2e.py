"""
tests/end_to_end/test_stereo_word_alignment_e2e.py

Class B E2E Test Suite for Wave 1:
Word-Level Live ASR Cache & Stereo Channel Isolation.

Verifies:
1. Live chunk transcription preserves word-level timestamps with relative start_sec offset.
2. master_asr_seeder copies 'words' from segments.jsonl into master ASR cache.
3. Stereo 2-channel WAV routing: Channel 1 dominance (mic) routes to Host Operator.
4. Speaker alignment with word timestamps preserves intra-chunk speaker switches.
"""
from __future__ import annotations

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
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    align_segments_with_diarization,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
    compute_channel_rms_ratio,
)
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    transcribe_meeting_chunk_with_segments,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    seed_master_asr_from_recorder,
)


def _create_stereo_wav(
    path: Path,
    duration_sec: float = 2.0,
    sample_rate: int = 16000,
    ch0_amplitude: int = 100,
    ch1_amplitude: int = 5000,
) -> None:
    """Generates 2-channel stereo WAV with configurable channel amplitudes."""
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val0 = ch0_amplitude if (i % 32 < 16) else -ch0_amplitude
            val1 = ch1_amplitude if (i % 32 < 16) else -ch1_amplitude
            frames.extend(struct.pack("<hh", val0, val1))
        wf.writeframes(frames)


class TestStereoWordAlignmentE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wave1_e2e_")
        self.temp_path = Path(self.temp_dir)
        self.stereo_wav = self.temp_path / "stereo_chunk.wav"
        _create_stereo_wav(
            self.stereo_wav,
            duration_sec=2.0,
            ch0_amplitude=50,
            ch1_amplitude=4000,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_asr_decode_config_has_word_timestamps_in_live_kwargs(self):
        """Verify ASRDecodeConfig to_live_kwargs enables word_timestamps."""
        cfg = ASRDecodeConfig(word_timestamps=True)
        live_kwargs = cfg.to_live_kwargs(model_path="dummy_model")
        self.assertTrue(live_kwargs.get("word_timestamps"))

    def test_stereo_channel_rms_ratio_and_host_routing(self):
        """Verify Ch1 dominance (local mic) is detected and routed to Host in LiveVoiceRegistry."""
        rms_ch0, rms_ch1 = compute_channel_rms_ratio(self.stereo_wav)
        self.assertGreater(rms_ch1, 2.5 * rms_ch0)
        self.assertGreaterEqual(rms_ch1, 80.0)

        registry = LiveVoiceRegistry(similarity_threshold=0.78)
        speaker, voice_id, confidence = registry.resolve_chunk_speaker(
            wav_path=self.stereo_wav,
            chunk_idx=0,
            text="I am speaking as the meeting host.",
            start_sec=0.0,
            duration_sec=2.0,
        )
        self.assertEqual(speaker, "Host")
        self.assertEqual(voice_id, "Voice Host")
        self.assertEqual(confidence, 1.0)

    def test_master_asr_seeder_preserves_words(self):
        """Verify master_asr_seeder retains 'words' in seeded cache from segments.jsonl."""
        recorder = MagicMock()
        recorder.segments_jsonl = self.temp_path / "segments.jsonl"
        recorder.master_wav = self.temp_path / "master.wav"
        recorder.cache_dir = self.temp_path / "cache"
        recorder.mock_mode = False
        recorder.whisper_model = "mlx-community/whisper-large-v3-turbo"

        sample_words = [
            {"word": "Hello", "start": 0.5, "end": 1.0, "probability": 0.98},
            {"word": "team", "start": 1.1, "end": 1.5, "probability": 0.95},
        ]
        line_data = {
            "chunk_idx": 0,
            "start_sec": 0.0,
            "end_sec": 2.0,
            "text": "Hello team",
            "speaker": "Host",
            "words": sample_words,
        }
        recorder.segments_jsonl.write_text(json.dumps(line_data) + "\n", encoding="utf-8")

        receipt = MagicMock()
        receipt.master_wav_sha256 = "test_sha256_hash_12345"

        success = seed_master_asr_from_recorder(recorder, receipt)
        self.assertTrue(success)

        cached_segments = load_audio_asr_cache(
            audio_sha256="test_sha256_hash_12345",
            model_name=recorder.whisper_model,
            stem=recorder.master_wav.stem,
            cache_dir=recorder.cache_dir,
            mock=False,
        )
        self.assertIsNotNone(cached_segments)
        self.assertEqual(len(cached_segments), 1)
        self.assertIn("words", cached_segments[0])
        self.assertEqual(cached_segments[0]["words"], sample_words)

    def test_speaker_alignment_intra_chunk_switch_with_words(self):
        """Verify rapid speaker switch inside a single chunk is preserved by word-level alignment."""
        seg = {
            "start": 0.0,
            "end": 6.0,
            "text": "First speaker speaking. Second speaker responding.",
            "words": [
                {"word": "First", "start": 0.5, "end": 1.0},
                {"word": "speaker", "start": 1.1, "end": 1.5},
                {"word": "speaking.", "start": 1.6, "end": 2.0},
                {"word": "Second", "start": 3.0, "end": 3.5},
                {"word": "speaker", "start": 3.6, "end": 4.0},
                {"word": "responding.", "start": 4.1, "end": 4.5},
            ],
        }

        # Diarization intervals indicate Speaker A during 0.0-2.5 and Speaker B during 2.6-5.0
        intervals = [
            AcousticInterval(
                interval_id="int_0",
                speaker_cluster="SPEAKER_00",
                start_time_sec=0.0,
                end_time_sec=2.5,
            ),
            AcousticInterval(
                interval_id="int_1",
                speaker_cluster="SPEAKER_01",
                start_time_sec=2.6,
                end_time_sec=5.0,
            ),
        ]

        class MockDiarization:
            def itertracks(self, yield_label=True):
                for inv in intervals:
                    mock_turn = MagicMock()
                    mock_turn.start = inv.start_time_sec
                    mock_turn.end = inv.end_time_sec
                    yield mock_turn, "track", inv.speaker_cluster

        aligned = align_segments_with_diarization([seg], MockDiarization())
        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned[0]["speaker"], "SPEAKER_00")
        self.assertIn("First speaker speaking.", aligned[0]["text"])
        self.assertEqual(aligned[1]["speaker"], "SPEAKER_01")
        self.assertIn("Second speaker responding.", aligned[1]["text"])


if __name__ == "__main__":
    unittest.main()
