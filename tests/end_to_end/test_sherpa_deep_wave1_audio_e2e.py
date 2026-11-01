"""End-to-End verification of Sherpa Deep RCA Wave 1:
Audio ASR IPC, Hybrid Diarization, Pre-Hash Header Repair, and Container Deconfliction.
"""
import io
import json
import os
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
    build_audio_asr_cache_key,
    load_audio_asr_cache,
)
from dual_pipeline.audio_ingestion_core.container_validator import repair_wav_riff_header
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
    _execute_chunk_transcription,
)
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    seed_master_asr_from_recorder,
)
from dual_pipeline.meeting_deconfliction import MeetingCollisionDetector
from dual_pipeline.naming import (
    is_live_meeting_container,
    resolve_meeting_container_dir,
    resolve_meeting_container_slug,
)
from dual_pipeline.transcript_pipeline_core.ingress import (
    _inspect_file_target,
    transcribe_audio_with_cache,
)
from dual_pipeline.transcript_pipeline_core.recorder import PipelineSpanRecorder


def _generate_synthetic_pcm_wav(
    file_path: Path,
    duration_sec: float = 1.0,
    sample_rate: int = 16000,
    channels: int = 2,
    ch0_active: bool = True,
    ch1_active: bool = True,
) -> None:
    """Generates a valid 16-bit PCM WAV file with specified channel activity."""
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_samples):
            # 440 Hz tone
            val = int(10000 * (1 if (i // 18) % 2 == 0 else -1))
            val0 = val if ch0_active else 0
            val1 = val if ch1_active else 0
            if channels == 2:
                frames.extend(struct.pack("<hh", val0, val1))
            else:
                frames.extend(struct.pack("<h", val0))
        wf.writeframes(bytes(frames))


class TestSherpaDeepWave1AudioE2E:
    """E2E suite verifying Wave 1 audio pipeline and container deconfliction."""

    def test_isolated_transcriber_ipc_segments_e2e(self, tmp_path: Path):
        """Verifies IsolatedMLXTranscriberClient preserves segments across IPC."""
        dummy_wav = tmp_path / "chunk_00001.wav"
        _generate_synthetic_pcm_wav(dummy_wav, duration_sec=0.2, channels=1)

        fake_segments = [
            {"start": 0.0, "end": 0.2, "text": "Hello world", "words": [{"word": "Hello", "start": 0.0, "end": 0.1}]}
        ]

        class DummyQueue:
            def __init__(self):
                self.items = []

            def put(self, item):
                self.items.append(item)

            def get(self, timeout=None):
                if self.items:
                    return self.items.pop(0)
                import queue
                raise queue.Empty()

        out_q = DummyQueue()
        msg = {
            "task_id": "task_001",
            "wav_path": str(dummy_wav),
            "backend": "mlx",
            "model_name": "dummy_model",
            "mock_mode": False,
        }

        with patch("dual_pipeline.live_meeting_recorder_core.chunk_transcriber.transcribe_meeting_chunk", return_value=("Hello world", fake_segments)):
            _execute_chunk_transcription(msg, out_q, default_model="dummy_model", mock_mode=False)

        assert len(out_q.items) == 1
        res = out_q.items[0]
        assert res["status"] == "SUCCESS"
        assert res["text"] == "Hello world"
        assert res["segments"] == fake_segments

        # Verify client extracts tuple when return_segments=True
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        extracted = client._extract_task_result(res, task_id="task_001", return_segments=True)
        assert extracted == ("Hello world", fake_segments)

        extracted_text_only = client._extract_task_result(res, task_id="task_001", return_segments=False)
        assert extracted_text_only == "Hello world"

    def test_master_asr_cache_seeding_and_hit_with_words_e2e(self, tmp_path: Path):
        """Verifies master ASR seeder writes words with language/model_id and mlx_transcriber hits cache."""
        segments_jsonl = tmp_path / "00_test_segments.jsonl"
        with open(segments_jsonl, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "start_sec": 0.0,
                "end_sec": 1.5,
                "verbatim": "Welcome to Project Beacon session",
                "speaker": "Drew Oakley",
                "words": [
                    {"word": "Welcome", "start": 0.0, "end": 0.4},
                    {"word": "to", "start": 0.4, "end": 0.6},
                    {"word": "digital", "start": 0.6, "end": 1.0},
                    {"word": "sherpa", "start": 1.0, "end": 1.3},
                    {"word": "session", "start": 1.3, "end": 1.5},
                ],
            }) + "\n")

        master_wav = tmp_path / "00_master_16k.wav"
        _generate_synthetic_pcm_wav(master_wav, duration_sec=1.5, channels=1)

        import hashlib
        master_sha = hashlib.sha256(master_wav.read_bytes()).hexdigest()

        receipt = MagicMock()
        receipt.master_wav_sha256 = master_sha

        recorder = MagicMock()
        recorder.segments_jsonl = segments_jsonl
        recorder.master_wav = master_wav
        recorder.transcript_cache_dir = tmp_path / "cache"
        recorder.whisper_model = "auto"
        recorder.language = "en"
        recorder.mock_mode = False

        ok = seed_master_asr_from_recorder(recorder, receipt)
        assert ok is True

        from dual_pipeline.model_registry import resolve_acoustic_asr
        resolved_model = resolve_acoustic_asr().model_id

        # Verify cached file on disk exists and contains words
        cached = load_audio_asr_cache(
            audio_sha256=master_sha,
            model_name=resolved_model,
            language="en",
            stem=master_wav.stem,
            cache_dir=recorder.transcript_cache_dir,
            mock=False,
        )
        assert cached is not None
        assert len(cached) == 1
        assert cached[0]["text"] == "Welcome to Project Beacon session"
        assert len(cached[0]["words"]) == 5

    def test_naming_rejects_cache_and_date_slugs_e2e(self, tmp_path: Path):
        """Verifies naming rejects .cache and date slugs from becoming meeting containers."""
        project_dir = tmp_path / "output" / "project_beacon" / "10-14-2026"
        meeting_dir = project_dir / "Sherpa_Connect_Cansu_Dan"
        cache_dir = meeting_dir / ".cache"
        chunks_dir = meeting_dir / "chunks"
        date_dir = project_dir

        meeting_dir.mkdir(parents=True)
        cache_dir.mkdir(parents=True)
        chunks_dir.mkdir(parents=True)

        (meeting_dir / "record_start_receipt.json").write_text("{}", encoding="utf-8")

        assert is_live_meeting_container(meeting_dir) is True
        assert is_live_meeting_container(cache_dir) is False
        assert is_live_meeting_container(chunks_dir) is False
        assert is_live_meeting_container(date_dir) is False

        # Resolution from inside .cache or chunks must return parent meeting_dir
        assert resolve_meeting_container_dir(cache_dir) == meeting_dir
        assert resolve_meeting_container_dir(chunks_dir) == meeting_dir
        assert resolve_meeting_container_slug(cache_dir) == "Sherpa_Connect_Cansu_Dan"
        assert resolve_meeting_container_slug(chunks_dir) == "Sherpa_Connect_Cansu_Dan"

    def test_preflight_deconfliction_reads_live_transcript_e2e(self, tmp_path: Path):
        """Verifies deconfliction reads *_live_transcript.md to detect sibling collision."""
        date_dir = tmp_path / "output" / "project_beacon" / "10-14-2026"
        sibling_dir = date_dir / "Connect_Cansu_PartnerAdvisory"
        sibling_dir.mkdir(parents=True)

        (sibling_dir / "record_start_receipt.json").write_text(json.dumps({
            "time_window": "04:30PM-05:00PM ET",
            "attendees": ["Cansu", "Drew Oakley"],
        }), encoding="utf-8")
        (sibling_dir / "00_10-14-2026_Connect_Cansu_PartnerAdvisory_live_transcript.md").write_text(
            "## Live Transcript\n\n[00:00:10] Drew Oakley: Welcome to Project Beacon architecture discussion.",
            encoding="utf-8",
        )

        detector = MeetingCollisionDetector(repo_root=tmp_path)
        col = detector.check_candidate_collision(
            project="project_beacon",
            date_str="10-14-2026",
            candidate_slug="Sherpa_Connect_Cansu_Dan",
            candidate_time="04:30PM-05:00PM ET",
            candidate_attendees=["Cansu", "Drew Oakley"],
            candidate_text="Drew Oakley: Welcome to Project Beacon architecture discussion.",
        )
        assert col.is_collision is True
        assert col.existing_slug == "Connect_Cansu_PartnerAdvisory"

    def test_ingress_wav_repair_pre_hash_and_segment_hydration_e2e(self, tmp_path: Path):
        """Verifies ingress repairs corrupt RIFF size pre-hash and preserves stable digest."""
        wav_file = tmp_path / "corrupted_header.wav"
        _generate_synthetic_pcm_wav(wav_file, duration_sec=0.5, channels=1)

        raw_bytes = bytearray(wav_file.read_bytes())
        # Corrupt the RIFF header chunk size (bytes 4-8) with zero
        raw_bytes[4:8] = b"\x00\x00\x00\x00"
        wav_file.write_bytes(bytes(raw_bytes))

        meta: Dict[str, Any] = {"transcript_cache_dir": str(tmp_path / "cache")}
        is_file, input_p, input_ext, hash1 = _inspect_file_target(str(wav_file), meta)

        assert is_file is True
        assert hash1 is not None

        # Re-inspecting should yield identical hash (idempotent, repaired once)
        _, _, _, hash2 = _inspect_file_target(str(wav_file), meta)
        assert hash1 == hash2

        # Verify structured_segments hydration
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        seg_data = [{"start": 0.0, "end": 0.5, "text": "hydrated segments", "speaker": "SPEAKER_00"}]
        (cache_dir / f"{hash1}.segments.json").write_text(json.dumps(seg_data), encoding="utf-8")

        with patch("dual_pipeline.transcript_pipeline_core.ingress.get_cached_audio_transcript", return_value="hydrated segments"):
            res_text = transcribe_audio_with_cache(str(wav_file), meta, PipelineSpanRecorder(), mock=True)
            assert res_text == "hydrated segments"
            assert meta.get("structured_segments") == seg_data
