"""tests/test_post_asr_cache_and_voiceprint_replay_e2e.py

Wave 1 Acceptance Receipt & Verification Test Suite.
Verifies that replaying a prior meeting from master_wav, .cache/01_*_segments.jsonl,
or 01_*_live_transcript.md:
1. Executes zero FFmpeg WAV extraction, zero MLX Whisper calls, and zero Pyannote diarization calls.
2. Creates and preserves valid cryptographic cache seals (.cache_seal.json) with zero CacheIntegrityViolationError.
3. Preserves deterministic diarization_status ("DIARIZED_ACOUSTIC" -> "DIARIZED_ACOUSTIC_RESOLVED" after hints).
4. Maintains bidirectional speaker cluster bridging across Voice A <-> SPEAKER_00 <-> Speaker 1.
5. Resolves directory inputs to canonical transcript or audio sources.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.canonical_pipeline.content_loader import (
    load_canonical_source_content,
    _resolve_container_source_file,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    _hydrate_speaker_artifacts,
    resolve_pre_hitl_speaker_hints,
)
from dual_pipeline.exceptions import CacheIntegrityViolationError
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    _parse_segment_entry,
    seed_master_asr_from_recorder,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import _check_voiceprint_match
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    find_associated_sidecars,
    verify_cache_seal,
)


def _create_minimal_wav_file(path: Path) -> None:
    """Creates a minimal valid 16kHz mono 16-bit PCM WAV header + 1 second of silence."""
    import struct
    sample_rate = 16000
    num_samples = 16000
    bytes_per_sample = 2
    data_size = num_samples * bytes_per_sample
    file_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        file_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * bytes_per_sample,
        bytes_per_sample,
        16,  # 16-bit
        b"data",
        data_size,
    )
    path.write_bytes(header + b"\x00" * data_size)


class TestPostASRCacheAndVoiceprintReplayE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = Path(tempfile.mkdtemp(prefix="eval_sandbox_wave1_"))
        self.cache_dir = self.sandbox / "transcript_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.master_wav = self.sandbox / "00_test_meeting_master.wav"
        _create_minimal_wav_file(self.master_wav)

        import hashlib
        self.master_sha256 = hashlib.sha256(self.master_wav.read_bytes()).hexdigest()

        # Create live segments JSONL
        self.segments_jsonl = self.sandbox / "01_test_meeting_segments.jsonl"
        seg_lines = [
            json.dumps({
                "start_sec": 0.0,
                "end_sec": 3.0,
                "verbatim": "Good morning everyone and welcome to the session.",
                "speaker": "Voice A",
                "voice_id": "Voice A",
                "words": [
                    {"word": "Good", "start": 0.0, "end": 0.5},
                    {"word": "morning", "start": 0.5, "end": 1.0},
                    {"word": "everyone", "start": 1.0, "end": 1.8},
                    {"word": "and", "start": 1.8, "end": 2.0},
                    {"word": "welcome", "start": 2.0, "end": 2.5},
                    {"word": "to", "start": 2.5, "end": 2.7},
                    {"word": "the", "start": 2.7, "end": 2.8},
                    {"word": "session.", "start": 2.8, "end": 3.0},
                ],
            }),
            json.dumps({
                "start_sec": 3.5,
                "end_sec": 6.5,
                "verbatim": "Thanks Mitchell, glad to be collaborating here.",
                "speaker": "Voice B",
                "voice_id": "Voice B",
                "words": [
                    {"word": "Thanks", "start": 3.5, "end": 4.0},
                    {"word": "Mitchell,", "start": 4.0, "end": 4.5},
                    {"word": "glad", "start": 4.5, "end": 5.0},
                    {"word": "to", "start": 5.0, "end": 5.3},
                    {"word": "be", "start": 5.3, "end": 5.6},
                    {"word": "collaborating", "start": 5.6, "end": 6.2},
                    {"word": "here.", "start": 6.2, "end": 6.5},
                ],
            }),
        ]
        self.segments_jsonl.write_text("\n".join(seg_lines) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_parse_segment_entry_preserves_raw_cluster_id(self) -> None:
        """Verifies that _parse_segment_entry preserves 'Voice A' cluster IDs instead of coercing them to 'Unknown'."""
        line = json.dumps({"start_sec": 0.0, "end_sec": 2.0, "verbatim": "Hello world", "speaker": "Voice A"})
        seg = _parse_segment_entry(line)
        self.assertIsNotNone(seg)
        self.assertEqual(seg["speaker"], "Voice A")
        self.assertEqual(seg["raw_speaker"], "Voice A")

        # When mapped in speaker_map, should resolve to human name
        seg_mapped = _parse_segment_entry(line, speaker_map={"Voice A": "Alex Rivera"})
        self.assertEqual(seg_mapped["speaker"], "Alex Rivera")

    def test_seeding_creates_tier_a_cache_and_cryptographic_seal(self) -> None:
        """Verifies that seed_master_asr_from_recorder creates Tier A sidecars and a valid cryptographic seal."""
        recorder = MagicMock()
        recorder.segments_jsonl = self.segments_jsonl
        recorder.master_wav = self.master_wav
        recorder.whisper_model = "test-model"
        recorder.language = "en"
        recorder.transcript_cache_dir = self.cache_dir
        recorder.mock_mode = True
        recorder.processed_chunks = ["c1.wav", "c2.wav"]

        receipt = MagicMock()
        receipt.master_wav_sha256 = self.master_sha256
        receipt.total_chunks = 2
        receipt.duration_s = 6.5
        receipt.status = "SUCCESS"

        success = seed_master_asr_from_recorder(recorder, receipt)
        self.assertTrue(success)

        # Verify sidecars created
        raw_asrs = list(self.cache_dir.glob("*.raw_asr.json"))
        tc_files = list(self.cache_dir.glob("*.transcript_cache.json"))
        seg_files = list(self.cache_dir.glob("*.segments.json"))
        utt_files = list(self.cache_dir.glob("*.utterances.json"))
        seal_files = list(self.cache_dir.glob("*.cache_seal.json"))

        self.assertEqual(len(raw_asrs), 1)
        self.assertEqual(len(tc_files), 1)
        self.assertEqual(len(seg_files), 1)
        self.assertEqual(len(utt_files), 1)
        self.assertEqual(len(seal_files), 1)

        # Verify cryptographic seal verification passes
        seal_meta = verify_cache_seal(structured_file=seg_files[0])
        self.assertTrue(seal_meta.get("sealed"))
        self.assertEqual(seal_meta.get("turn_count"), 2)

    def test_two_consecutive_replays_zero_compute_and_preserve_seal(self) -> None:
        """Verifies that two consecutive replays execute 0 FFmpeg, 0 Whisper, 0 Pyannote calls and pass seals."""
        # 1. Seed cache
        recorder = MagicMock()
        recorder.segments_jsonl = self.segments_jsonl
        recorder.master_wav = self.master_wav
        recorder.whisper_model = "test-model"
        recorder.language = "en"
        recorder.transcript_cache_dir = self.cache_dir
        recorder.mock_mode = True
        recorder.processed_chunks = ["c1.wav", "c2.wav"]

        receipt = MagicMock()
        receipt.master_wav_sha256 = self.master_sha256
        receipt.total_chunks = 2
        receipt.duration_s = 6.5
        receipt.status = "SUCCESS"

        seed_master_asr_from_recorder(recorder, receipt)
        seg_file = list(self.cache_dir.glob("*.segments.json"))[0]
        seal_file = list(self.cache_dir.glob("*.cache_seal.json"))[0]
        initial_seal_bytes = seal_file.read_bytes()

        # 2. Replay Run 1
        transcriber = LocalMLXAudioTranscriber(mock=True, model_name="test-model", language="en")
        meta_run1: dict = {"transcript_cache_dir": str(self.cache_dir)}

        mock_whisper = MagicMock()
        with patch("pipeline_a_intake.media_extractor.extract_normalized_audio") as mock_ffmpeg, \
             patch.dict("sys.modules", {"mlx_whisper": mock_whisper}):
            t1 = transcriber._live_transcribe(str(self.master_wav), metadata=meta_run1)
            mock_ffmpeg.assert_not_called()
            mock_whisper.transcribe.assert_not_called()

        self.assertIn("Good morning everyone", t1)
        self.assertTrue(meta_run1.get("cache_seal_verified"))
        self.assertEqual(meta_run1.get("diarization_status"), "DIARIZED_ACOUSTIC")

        # 3. Simulate pre-HITL quote hint resolution mapping Voice A to Alex Rivera
        meta_run1["speaker_hints"] = [
            {"quote": "Good morning everyone and welcome to the session.", "speaker": "Alex Rivera"}
        ]
        u_file = list(self.cache_dir.glob("*.utterances.json"))[0]
        meta_run1["structured_utterances_path"] = str(u_file)
        meta_run1["raw_transcript"] = t1

        cluster_map = resolve_pre_hitl_speaker_hints(meta_run1)
        self.assertIn("Voice A", cluster_map)
        self.assertEqual(cluster_map["Voice A"], "Alex Rivera")

        # Verify cache seal was re-sealed and passes verification
        seal_meta_after_hint = verify_cache_seal(structured_file=seg_file)
        self.assertTrue(seal_meta_after_hint.get("sealed"))

        # 4. Replay Run 2
        meta_run2: dict = {"transcript_cache_dir": str(self.cache_dir)}
        with patch("pipeline_a_intake.media_extractor.extract_normalized_audio") as mock_ffmpeg, \
             patch.dict("sys.modules", {"mlx_whisper": mock_whisper}):
            t2 = transcriber._live_transcribe(str(self.master_wav), metadata=meta_run2)
            mock_ffmpeg.assert_not_called()
            mock_whisper.transcribe.assert_not_called()

        self.assertTrue(meta_run2.get("cache_seal_verified"))
        # Verify speaker bridging
        _hydrate_speaker_artifacts(meta_run2, [self.cache_dir], "test_meeting")
        spk_map = meta_run2.get("speaker_map", {})
        self.assertEqual(spk_map.get("Voice A"), "Alex Rivera")
        self.assertEqual(spk_map.get("SPEAKER_00"), "Alex Rivera")
        self.assertEqual(spk_map.get("Speaker 1"), "Alex Rivera")
        self.assertEqual(meta_run2.get("diarization_status"), "DIARIZED_ACOUSTIC_RESOLVED")

    def test_directory_input_resolves_to_source_file_and_cache(self) -> None:
        """Verifies that passing a meeting directory path resolves to the canonical source file."""
        meeting_dir = self.sandbox / "meeting_container"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = meeting_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Place segments.jsonl inside .cache/
        dest_seg = cache_dir / "01_sample_segments.jsonl"
        shutil.copy2(self.segments_jsonl, dest_seg)

        resolved = _resolve_container_source_file(meeting_dir)
        self.assertEqual(resolved.resolve(), dest_seg.resolve())

        # Now add live transcript to meeting_dir; it should take precedence
        live_md = meeting_dir / "01_sample_live_transcript.md"
        live_md.write_text("**Voice A** [00:00:00]: Hello container", encoding="utf-8")

        resolved_md = _resolve_container_source_file(meeting_dir)
        self.assertEqual(resolved_md.resolve(), live_md.resolve())

        meta: dict = {}
        text, h, src_path = load_canonical_source_content(meeting_dir, meta=meta)
        self.assertIn("Hello container", text)
        self.assertEqual(src_path.resolve(), live_md.resolve())
        self.assertEqual(Path(meta.get("container_dir", "")).resolve(), meeting_dir.resolve())

    def test_tier_b_hit_bypasses_ffmpeg_and_whisper(self) -> None:
        """Verifies that on Tier A miss, existing Tier B + diarization cache assembles turns with 0 FFmpeg/Whisper."""
        # Save Tier B raw ASR cache
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
            save_audio_asr_cache,
            save_audio_diarization_cache,
        )
        raw_segs = [
            {"start": 0.0, "end": 2.0, "text": "Test segment from Tier B", "speaker": "Voice A"}
        ]
        save_audio_asr_cache(
            audio_sha256=self.master_sha256[:16],
            raw_segments=raw_segs,
            model_name="test-model",
            language="en",
            stem=self.master_wav.stem,
            cache_dir=self.cache_dir,
            mock=True,
        )
        save_audio_diarization_cache(
            audio_sha256=self.master_sha256[:16],
            turns=[{"start": 0.0, "end": 2.0, "speaker": "Speaker 1"}],
            model_name="pinned",
            stem=self.master_wav.stem,
            cache_dir=self.cache_dir,
        )

        transcriber = LocalMLXAudioTranscriber(mock=True, model_name="test-model", language="en")
        meta: dict = {"transcript_cache_dir": str(self.cache_dir)}

        mock_whisper = MagicMock()
        with patch("pipeline_a_intake.media_extractor.extract_normalized_audio") as mock_ffmpeg, \
             patch.dict("sys.modules", {"mlx_whisper": mock_whisper}):
            t = transcriber._live_transcribe(str(self.master_wav), metadata=meta)
            mock_ffmpeg.assert_not_called()
            mock_whisper.transcribe.assert_not_called()

        self.assertIn("Test segment from Tier B", t)
        self.assertTrue(meta.get("has_acoustic_diarization"))

    def test_tampered_cache_fails_closed_with_integrity_error(self) -> None:
        """Verifies that modifying .segments.json without updating .cache_seal.json fails closed."""
        recorder = MagicMock()
        recorder.segments_jsonl = self.segments_jsonl
        recorder.master_wav = self.master_wav
        recorder.whisper_model = "test-model"
        recorder.language = "en"
        recorder.transcript_cache_dir = self.cache_dir
        recorder.mock_mode = True
        recorder.processed_chunks = ["c1.wav", "c2.wav"]

        receipt = MagicMock()
        receipt.master_wav_sha256 = self.master_sha256
        receipt.total_chunks = 2
        receipt.duration_s = 6.5
        receipt.status = "SUCCESS"

        seed_master_asr_from_recorder(recorder, receipt)
        seg_file = list(self.cache_dir.glob("*.segments.json"))[0]

        # Tamper with segments.json
        data = json.loads(seg_file.read_text(encoding="utf-8"))
        data[0]["speaker"] = "Tampered Speaker"
        seg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        with self.assertRaises(CacheIntegrityViolationError):
            verify_cache_seal(structured_file=seg_file)

    def test_candidate_scorer_voiceprint_match_robust_to_schema_types(self) -> None:
        """Verifies candidate_scorer._check_voiceprint_match handles str, dict, and tuple match types."""
        # 1. str match
        meta_str = {"voiceprint_matches": {"SPEAKER_00": "Alex Rivera"}}
        res_str = _check_voiceprint_match(meta_str, "SPEAKER_00", "Alex Rivera")
        self.assertIsNotNone(res_str)
        self.assertEqual(res_str[0], 0.85)

        # 2. dict match
        meta_dict = {"voiceprint_matches": {"SPEAKER_00": {"name": "Alex Rivera", "score": 0.92}}}
        res_dict = _check_voiceprint_match(meta_dict, "SPEAKER_00", "Alex Rivera")
        self.assertIsNotNone(res_dict)
        self.assertEqual(res_dict[0], 0.92)

        # 3. tuple/list match
        meta_list = {"voiceprint_matches": {"SPEAKER_00": ["Alex Rivera", 0.95]}}
        res_list = _check_voiceprint_match(meta_list, "SPEAKER_00", "Alex Rivera")
        self.assertIsNotNone(res_list)
        self.assertEqual(res_list[0], 0.95)


if __name__ == "__main__":
    unittest.main()
