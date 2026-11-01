"""
tests/test_post_meeting_diarization_handoff_e2e.py

End-to-end test for Wave 2: Live Voice Clustering & Post-Stop Acoustic Diarization.
Verifies:
1. Multi-speaker stereo WAV channel separation and preservation through Stage U0.
2. Unbiased Master ASR cache seeding from chunk segments (eliminating Voice A bias).
3. Standardized 21-D acoustic feature vectors preventing multi-speaker collapse into Voice A.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import wave
import numpy as np

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    LocalMLXAudioTranscriber,
    _flatten_attendee_roster,
)
from dual_pipeline.audio_ingestion_core.speaker_alignment import align_segments_via_stereo_energy
from dual_pipeline.audio_ingestion_core.audio_asr_cache import load_audio_asr_cache
from dual_pipeline.live_meeting_recorder_core.acoustic_features import extract_acoustic_signature
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def _create_synthetic_stereo_wav(
    path: Path,
    sr: int = 16000,
    duration_sec: float = 6.0,
) -> None:
    """Creates a stereo WAV file where Ch0 is active 0-3s and Ch1 is active 3-6s."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sr * duration_sec)
    half_samples = int(sr * (duration_sec / 2.0))

    ch0_data = np.zeros(num_samples, dtype=np.int16)
    ch1_data = np.zeros(num_samples, dtype=np.int16)

    # First half: Ch0 active (remote speaker, 400Hz), Ch1 silent
    t0 = np.linspace(0, duration_sec / 2.0, half_samples, endpoint=False)
    ch0_data[:half_samples] = (2000.0 * np.sin(2.0 * np.pi * 400.0 * t0)).astype(np.int16)

    # Second half: Ch1 active (local mic / host, 200Hz), Ch0 silent
    t1 = np.linspace(0, duration_sec / 2.0, num_samples - half_samples, endpoint=False)
    ch1_data[half_samples:] = (2000.0 * np.sin(2.0 * np.pi * 200.0 * t1)).astype(np.int16)

    stereo_interleaved = np.empty(num_samples * 2, dtype=np.int16)
    stereo_interleaved[0::2] = ch0_data
    stereo_interleaved[1::2] = ch1_data

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo_interleaved.tobytes())


def _create_synthetic_mono_voice_wav(
    path: Path,
    fundamental_hz: float,
    harmonics: list[float],
    sr: int = 16000,
    duration_sec: float = 3.0,
) -> None:
    """Creates a mono WAV file with rich harmonic structure mimicking a human voice."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(sr * duration_sec)
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)
    sig = np.sin(2.0 * np.pi * fundamental_hz * t)
    for i, h_freq in enumerate(harmonics, start=2):
        sig += (1.0 / i) * np.sin(2.0 * np.pi * h_freq * t)
    max_val = np.max(np.abs(sig))
    if max_val > 0:
        sig = (sig / max_val) * 3000.0
    int_data = sig.astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int_data.tobytes())


class TestPostMeetingDiarizationHandoffE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_diar_handoff_e2e_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stereo_wav_speaker_separation_and_u0_preservation(self) -> None:
        """
        Verify that a 2-channel stereo WAV separates remote and host speakers into
        SPEAKER_00 and SPEAKER_01, persists speaker mappings, and preserves
        VERIFIED_STEREO_SEPARATION throughout Stage U0 ingestion without downgrading.
        """
        stereo_wav = self.temp_path / "meeting_stereo.wav"
        _create_synthetic_stereo_wav(stereo_wav, duration_sec=6.0)

        segments = [
            {"start": 0.0, "end": 3.0, "text": "This is remote speaking on channel zero."},
            {"start": 3.0, "end": 6.0, "text": "This is local host speaking on channel one."},
        ]

        # 1. Align via stereo energy
        aligned = align_segments_via_stereo_energy(segments, stereo_wav)
        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned[0]["speaker"], "SPEAKER_00")
        self.assertEqual(aligned[1]["speaker"], "SPEAKER_01")
        self.assertEqual(aligned[0]["channel"], 0)
        self.assertEqual(aligned[1]["channel"], 1)

        # 2. LocalMLXAudioTranscriber._format_diarized_json with metadata
        transcriber = BaseAudioTranscriber(mock=False)
        metadata: dict[str, any] = {
            "project": "project_lens",
            "meeting_name": "Stereo Handoff Test",
            "date": "10/15/2026",
            "time": "14:00",
            "classification": "confidential",
            "attendees": {"PartnerAdvisory": ["PartnerAdvisoryTeam"], "NEA": ["Daniel Sabet"]},
            "diarization_status": "VERIFIED_STEREO_SEPARATION",
            "has_acoustic_diarization": True,
        }

        formatted = transcriber._format_diarized_json({"segments": aligned}, metadata=metadata)
        self.assertIn("**Speaker 1** [00:00:00]:", formatted)
        self.assertIn("**Speaker 2** [00:00:03]:", formatted)
        self.assertIn("acoustic_speaker_map", metadata)
        self.assertEqual(metadata["acoustic_speaker_map"].get("SPEAKER_00"), "Speaker 1")
        self.assertEqual(metadata["acoustic_speaker_map"].get("SPEAKER_01"), "Speaker 2")

        # 3. Stage U0 Ingestion detection
        u0_status = U0IngestionBoundary._detect_diarization_status(formatted, metadata)
        self.assertEqual(u0_status, "VERIFIED_STEREO_SEPARATION")
        self.assertNotEqual(u0_status, "UNDIARIZED")

        # 4. Verify flattened attendee roster extraction
        flattened = _flatten_attendee_roster(metadata)
        self.assertIn("PartnerAdvisoryTeam", flattened)
        self.assertIn("Daniel Sabet", flattened)
        self.assertEqual(len(flattened), 2)

    def test_master_asr_seeder_preserves_unbiased_segments(self) -> None:
        """
        Verify that master_asr_seeder seeds raw chunk segments into the master ASR cache
        without forcing Voice A or single-speaker bias, allowing clean downstream diarization.
        """
        cache_dir = self.temp_path / "transcript_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        segments_jsonl = self.temp_path / "01_10_15_2026_test_meeting_segments.jsonl"
        master_wav = self.temp_path / "master.wav"
        _create_synthetic_mono_voice_wav(master_wav, fundamental_hz=200.0, harmonics=[400.0, 600.0])

        with open(segments_jsonl, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "chunk_idx": 0, "start_sec": 0.0, "end_sec": 5.0,
                "text": "First segment content.", "verbatim": "First segment content.",
                "speaker": "Voice A",
            }) + "\n")
            f.write(json.dumps({
                "chunk_idx": 1, "start_sec": 5.0, "end_sec": 10.0,
                "text": "Second segment content.", "verbatim": "Second segment content.",
                "speaker": "Voice B",
            }) + "\n")

        mock_recorder = MagicMock()
        mock_recorder.segments_jsonl = segments_jsonl
        mock_recorder.master_wav = master_wav
        mock_recorder.transcript_cache_dir = cache_dir
        mock_recorder.whisper_model = "mlx-community/whisper-large-v3-turbo"
        mock_recorder.language = "en"
        mock_recorder.mock_mode = False

        mock_receipt = MagicMock()
        mock_receipt.master_wav_sha256 = "dummy_master_sha256_hash_12345"

        success = seed_master_asr_from_recorder(mock_recorder, mock_receipt)
        self.assertTrue(success)

        cached_segments = load_audio_asr_cache(
            audio_sha256="dummy_master_sha256_hash_12345",
            model_name="mlx-community/whisper-large-v3-turbo",
            language="en",
            stem=master_wav.stem,
            cache_dir=cache_dir,
            mock=False,
        )
        self.assertIsNotNone(cached_segments)
        self.assertEqual(len(cached_segments), 2)
        # Verify that Voice A / Voice B biases were stripped
        for seg in cached_segments:
            self.assertEqual(seg.get("speaker"), "Unknown")
            self.assertFalse(str(seg.get("speaker")).startswith("Voice "))

    def test_standardized_acoustic_features_prevent_voice_collapse(self) -> None:
        """
        Verify that standardized 21-D acoustic feature vectors (zero mean, unit variance)
        maintain proper separation across distinct voices, preventing collapse into Voice A.
        """
        wav_voice1 = self.temp_path / "voice1_female_high.wav"
        wav_voice2 = self.temp_path / "voice2_male_low.wav"
        wav_voice1_rep = self.temp_path / "voice1_female_repeat.wav"

        # Female voice: high F0 (260Hz) with high-frequency harmonics
        _create_synthetic_mono_voice_wav(wav_voice1, fundamental_hz=260.0, harmonics=[520.0, 780.0, 1040.0])
        _create_synthetic_mono_voice_wav(wav_voice1_rep, fundamental_hz=265.0, harmonics=[530.0, 795.0, 1060.0])

        # Male voice: low F0 (110Hz) with lower harmonics
        _create_synthetic_mono_voice_wav(wav_voice2, fundamental_hz=110.0, harmonics=[220.0, 330.0, 440.0])

        feat1 = extract_acoustic_signature(wav_voice1)
        feat2 = extract_acoustic_signature(wav_voice2)
        feat1_rep = extract_acoustic_signature(wav_voice1_rep)

        self.assertEqual(len(feat1), 21)
        self.assertEqual(len(feat2), 21)
        self.assertAlmostEqual(float(np.linalg.norm(feat1)), 1.0, places=4)
        self.assertAlmostEqual(float(np.linalg.norm(feat2)), 1.0, places=4)

        # Cosine similarity between distinct voices should be well separated
        cos_diff = float(np.dot(feat1, feat2))
        cos_same = float(np.dot(feat1, feat1_rep))

        self.assertLess(cos_diff, 0.65, f"Distinct voices should have cosine similarity < 0.65, got {cos_diff}")
        self.assertGreater(cos_same, 0.70, f"Same voice should have cosine similarity > 0.70, got {cos_same}")

        # Live voice registry: should allocate Voice A for voice 1 and Voice B for voice 2
        registry = LiveVoiceRegistry(similarity_threshold=0.68)

        spk1, vid1, conf1 = registry.resolve_chunk_speaker(wav_voice1, chunk_idx=0, text="Turn from speaker 1")
        self.assertEqual(vid1, "Voice A")

        spk2, vid2, conf2 = registry.resolve_chunk_speaker(wav_voice2, chunk_idx=1, text="Turn from speaker 2")
        self.assertEqual(vid2, "Voice B", "Distinct voice must allocate Voice B instead of collapsing into Voice A")

        spk1_rep, vid1_rep, conf1_rep = registry.resolve_chunk_speaker(
            wav_voice1_rep, chunk_idx=2, text="Another turn from speaker 1"
        )
        self.assertEqual(vid1_rep, "Voice A", "Repeat turn from speaker 1 must re-match Voice A")
        self.assertEqual(len(registry.voice_clusters), 2)


if __name__ == "__main__":
    unittest.main()
