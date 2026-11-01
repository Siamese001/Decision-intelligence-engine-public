"""
E2E verification of Wave 3: Stage U0 Dual-Directory Hydration & ASR Cache Alignment
(BUG-11, BUG-12, BUG-13, BUG-14).
"""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from dual_pipeline.audio_ingestion_core.audio_asr_cache import load_audio_asr_cache
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from types import SimpleNamespace
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder


def _write_test_wav(path: Path, duration_sec: float = 10.0, sample_rate: int = 16000) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x01\x00" * n_frames)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _DummyStateMachine:
    def __init__(self) -> None:
        self.frozen: Dict[str, str] = {}
        self.permanently_frozen: set[str] = set()
        self.passing_stages: set[str] = set()

    def is_frozen(self, key: str) -> bool:
        return key in self.frozen

    def register_deliverable_pass(self, key: str, content: str) -> None:
        self.frozen[key] = content
        self.permanently_frozen.add(key)
        self.passing_stages.add(key)


def test_u0_dual_directory_hydration_enriches_time_attendees_hints_and_acoustic(tmp_path: Path) -> None:
    """
    Verifies BUG-11 and BUG-14:
    - Manifest in output/transcript_cache/<project>/<date>/ is discovered even when
      container_dir points to output/<project>/<date>/<slug>/
    - Time window is hydrated from started_at and stopped_at (not +30m fallback)
    - Flat CLI attendee string is enriched with GraphDB role and org from manifest
    - QUEUED speaker hints from speaker_hints_queue.json are preserved into turn_hints
    - Acoustic evidence receipt clusters are loaded into metadata['acoustic_clusters']
    """
    cache_dir = tmp_path / "output" / "transcript_cache" / "NEE" / "04-16-2026"
    container_dir = tmp_path / "output" / "NEE" / "04-16-2026" / "1138am_et_touch_base_with_amit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    container_dir.mkdir(parents=True, exist_ok=True)

    wav_path = cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_recording.wav"
    _write_test_wav(wav_path, duration_sec=4.0)

    manifest_data = {
        "session_id": "sess_0416",
        "project": "NEE",
        "meeting": "Touch Base with Amit",
        "started_at": "2026-04-16T11:38:00-04:00",
        "stopped_at": "2026-04-16T12:19:00-04:00",
        "attendees": [
            {"name": "Amit Ayer", "role": "Managing Director", "org": "Deloitte"},
            {"name": "Speaker_Beta Chacko", "role": "Senior Advisor", "org": "NEE"},
        ],
    }
    (cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_manifest.json").write_text(
        json.dumps(manifest_data), encoding="utf-8"
    )

    queue_hints = [
        {
            "event_id": "evt_1",
            "speaker": "Speaker_Beta Chacko",
            "quote": "We should prioritize the commercial model.",
            "timestamp_sec": 14.5,
            "status": "QUEUED",
        }
    ]
    (cache_dir / "speaker_hints_queue.json").write_text(json.dumps(queue_hints), encoding="utf-8")

    acoustic_receipt = {
        "status": "VERIFIED",
        "clusters": [
            {"cluster_id": "SPEAKER_00", "dominant_channel": "left", "turn_count": 12},
            {"cluster_id": "SPEAKER_01", "dominant_channel": "right", "turn_count": 9},
        ],
    }
    (cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_acoustic_evidence_receipt.json").write_text(
        json.dumps(acoustic_receipt), encoding="utf-8"
    )

    metadata: Dict[str, Any] = {
        "output_dir": str(container_dir),
        "attendees": "Amit Ayer, Speaker_Beta Chacko",
    }

    hydrate_live_container_artifacts(metadata, input_path=str(wav_path))

    assert metadata["time"] == "11:38 AM - 12:19 PM ET"
    assert isinstance(metadata["attendees"], list)
    assert len(metadata["attendees"]) == 2
    assert metadata["attendees"][0]["role"] == "Managing Director"
    assert metadata["attendees"][1]["org"] == "NEE"
    assert len(metadata["turn_hints"]) == 1
    assert metadata["turn_hints"][0]["speaker"] == "Speaker_Beta Chacko"
    assert metadata["turn_hints"][0]["status"] == "QUEUED"
    assert len(metadata["acoustic_clusters"]) == 2


def test_u0_hydration_executes_before_load_source_content(tmp_path: Path) -> None:
    """
    Verifies BUG-12:
    _execute_u0 calls hydrate_live_container_artifacts BEFORE _load_source_content
    so that metadata['attendees'], metadata['time'], and metadata['turn_hints']
    are already populated when audio transcription runs.
    """
    cache_dir = tmp_path / "output" / "transcript_cache" / "NEE" / "04-16-2026"
    container_dir = tmp_path / "output" / "NEE" / "04-16-2026" / "1138am_et_touch_base_with_amit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    container_dir.mkdir(parents=True, exist_ok=True)

    wav_path = cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_recording.wav"
    _write_test_wav(wav_path, duration_sec=2.0)

    manifest_data = {
        "started_at": "2026-04-16T11:38:00-04:00",
        "stopped_at": "2026-04-16T12:10:00-04:00",
        "attendees": [{"name": "Amit Ayer", "role": "MD", "org": "Deloitte"}],
    }
    (cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_manifest.json").write_text(
        json.dumps(manifest_data), encoding="utf-8"
    )

    pipeline = CanonicalAgenticPipeline.__new__(CanonicalAgenticPipeline)
    observed_meta_during_load: Dict[str, Any] = {}

    def _fake_load_source_content(
        input_path: Any, meta_arg: Dict[str, Any], presentation_path: Any = None
    ) -> tuple[str, str, Path]:
        observed_meta_during_load["time"] = meta_arg.get("time")
        observed_meta_during_load["attendees"] = meta_arg.get("attendees")
        return "[00:00:01] **Amit Ayer**: Hello team.", "hash_1234", wav_path

    tracker = SimpleNamespace(
        start_stage=lambda *a, **kw: None,
        complete_stage=lambda *a, **kw: None,
    )
    meta: Dict[str, Any] = {"output_dir": str(container_dir), "attendees": "Amit Ayer"}
    with (
        patch.object(pipeline, "_load_source_content", side_effect=_fake_load_source_content),
        patch("dual_pipeline.canonical_pipeline.pipeline.check_idempotent_or_continue", return_value=None),
    ):
        pipeline._execute_u0(
            input_path=str(wav_path),
            meta=meta,
            presentation_path=None,
            attachments=None,
            attachment_dir=None,
            signal_envelope=None,
            output_dir=str(container_dir),
            mutate_graph=False,
            tracker=tracker,
        )

    assert observed_meta_during_load["time"] == "11:38 AM - 12:10 PM ET"
    assert isinstance(observed_meta_during_load["attendees"], list)
    assert observed_meta_during_load["attendees"][0]["role"] == "MD"
    assert meta.get("source_hash") == "hash_1234"
    assert meta.get("input_hash") == "hash_1234"


def test_asr_cache_alignment_word_synthesis_and_checkpoint_invalidation(tmp_path: Path) -> None:
    """
    Verifies BUG-13:
    1. _seed_master_asr_cache synthesizes interpolated word timestamps and hits in
       LocalMLXAudioTranscriber even when language=None (matching 'en' seeded cache)
       and when audio resides in transcript_cache while output_dir is the container dir.
    2. Checkpoint memoizer invalidates a stale 1-turn output_1.ckpt.md when the WAV
       is replaced with a full multi-turn recording.
    """
    cache_dir = tmp_path / "output" / "transcript_cache" / "NEE" / "04-16-2026"
    container_dir = tmp_path / "output" / "NEE" / "04-16-2026" / "1138am_et_touch_base_with_amit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    container_dir.mkdir(parents=True, exist_ok=True)

    wav_path = cache_dir / "00_04-16-2026_1138am_et_touch_base_with_amit_recording.wav"
    audio_sha = _write_test_wav(wav_path, duration_sec=10.0)

    segments_jsonl = cache_dir / "segments.jsonl"
    segs = [
        {"chunk_seq": 0, "start_sec": 0.0, "end_sec": 5.0, "text": "Good morning everyone let us begin"},
        {"chunk_seq": 1, "start_sec": 5.0, "end_sec": 10.0, "text": "Today we review the architecture handoff"},
    ]
    segments_jsonl.write_text("\n".join(json.dumps(s) for s in segs) + "\n", encoding="utf-8")

    recorder = SimpleNamespace(
        segments_jsonl=segments_jsonl,
        master_wav=wav_path,
        transcript_cache_dir=cache_dir,
        output_dir=container_dir,
        clean_slug="1138am_et_touch_base_with_amit",
        processed_chunks=[0, 1],
        whisper_model="mlx-community/whisper-large-v3-turbo",
        language=None,
        mock_mode=False,
    )
    receipt = SimpleNamespace(
        master_wav_sha256=audio_sha,
        total_chunks=2,
        duration_s=10.0,
    )
    seeded_ok = seed_master_asr_from_recorder(recorder, receipt)
    assert seeded_ok is True

    # Verify load_audio_asr_cache matches when language=None even though seeded with 'en'
    cached = load_audio_asr_cache(
        audio_sha256=audio_sha,
        model_name="mlx-community/whisper-large-v3-turbo",
        language=None,
        stem=wav_path.stem,
        cache_dir=cache_dir,
    )
    assert cached is not None
    assert len(cached) == 2
    assert all(isinstance(s.get("words"), list) and len(s["words"]) > 0 for s in cached)

    # Verify LocalMLXAudioTranscriber hits the cache in wav_path.parent even when output_dir is container_dir
    transcriber = LocalMLXAudioTranscriber(
        model_name="mlx-community/whisper-large-v3-turbo",
        mock=False,
        language=None,
    )
    meta: Dict[str, Any] = {"output_dir": str(container_dir)}
    with (
        patch("pipeline_a_intake.media_extractor.extract_normalized_audio", return_value=wav_path),
        patch("dual_pipeline.memory_guard.assert_memory_headroom"),
        patch.object(transcriber, "_diarize_and_align", side_effect= lambda segs, *a, **kw: segs),
    ):
        res_text = transcriber._live_transcribe(str(wav_path), metadata=meta)
    assert "Good morning everyone" in res_text
    assert meta.get("audio_sha256") == audio_sha
    assert meta.get("transcript_cache_dir") == str(cache_dir)

    # Verify stale 1-turn checkpoint invalidation when source_hash changes or turn count is truncated
    att_dir = container_dir / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True, exist_ok=True)
    stale_ckpt = (
        "# Forensic Transcript\n\n"
        + ("Header padding " * 20)
        + "\n**[00:00:01] Speaker 1**: Single truncated turn from 45s WAV.\n"
    )
    (att_dir / "output_1.ckpt.md").write_text(stale_ckpt, encoding="utf-8")
    (att_dir / "metadata.json").write_text(json.dumps({"source_hash": "old_45s_wav_sha"}), encoding="utf-8")

    sm = _DummyStateMachine()
    new_meta: Dict[str, Any] = {
        "source_hash": audio_sha,
        "input_hash": audio_sha,
        "turn_count": 12,
    }
    memoized = memoize_substage_checkpoints(
        container_dir, sm, new_meta, CanonicalModality.AUDIO
    )
    assert memoized["output_1"] is False
    assert not sm.is_frozen("output_1")
