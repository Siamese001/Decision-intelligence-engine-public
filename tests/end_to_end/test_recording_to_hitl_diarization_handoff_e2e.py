"""
Wave 5 End-to-End Test: Full Recording Stop to HITL Diarization Handoff Lifecycle.

Covers the complete handoff chain across Phases 1-4 (BUG-01 through BUG-20):
1. Post-meeting stop handoff, chunk migration, and canonical CLI command generation
2. Master ASR seeding (.raw_asr.json) with unified cache key and segment schema
3. Early Stage U0 live container hydration (00_*_manifest.json, speaker_hints_queue.json,
   00_*_acoustic_evidence_receipt.json, and rich attendee roster preservation)
4. BaseAudioTranscriber._format_diarized_json cluster_label_map isolation (zero speaker_map pollution)
5. Stage C0-PA & AudioSpeakerAttributionEngine per-turn quote hint matching on
   `**Speaker N** [HH:MM:SS]:` turns, 1-to-1 complementary elimination, and interactive HITL
   2-turn review context handoff.
"""

from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_ingestion_core.audio_asr_cache import load_audio_asr_cache
from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.canonical_pipeline.c0_pa_runner import execute_c0_pa
from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    seed_master_asr_from_recorder,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    migrate_staged_files,
)
from dual_pipeline.speaker_attribution_core.review_context_render import (
    validate_visible_speaker_context,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class _StubTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata: Dict[str, Any] | None = None) -> str:
        return ""


class _NoOpStage:
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return ctx


class _NoOpTracker:
    def start_stage(self, stage: Any) -> None:
        pass

    def complete_stage(self, stage: Any, **kwargs: Any) -> None:
        pass

    def end_stage(self, stage: Any, **kwargs: Any) -> None:
        pass


def _make_stub_pipeline() -> Any:
    return SimpleNamespace(
        c0_native=_NoOpStage(),
        c0_undiarized=_NoOpStage(),
        c05_title=_NoOpStage(),
        c06_role=_NoOpStage(),
        pa_resolver=_NoOpStage(),
    )


def _write_synthetic_wav(wav_path: Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> str:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    pcm = struct.pack(f"<{n_samples}h", *([600, -600] * (n_samples // 2)))
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return hashlib.sha256(wav_path.read_bytes()).hexdigest()


def test_full_recording_stop_to_hitl_diarization_handoff_e2e(tmp_path: Path) -> None:
    """
    End-to-end verification of the complete handoff pipeline:
    Staging chunk migration -> canonical ingest command generation -> master ASR cache seeding ->
    Stage U0 container hydration -> diarized transcript formatting -> Stage C0-PA quote-hint
    attribution & 1-to-1 complementary elimination -> Output 01 resolved speaker headers.
    """
    staging_dir = tmp_path / "staging_root" / "output"
    worktree_dir = tmp_path / "worktree_root"
    container_dir = worktree_dir / "output" / "NEE" / "10-16-2026" / "1034am_et_ed_amit"
    container_dir.mkdir(parents=True, exist_ok=True)
    wav_path = container_dir / "00_10-16-2026_1034am_et_ed_amit_recording.wav"

    # 1. Stage chunk migration & root staging cleanup (BUG-01..BUG-03)
    staged_chunks = staging_dir / "_chunks_Ed_Amit_10-16-2026"
    staged_chunks.mkdir(parents=True, exist_ok=True)
    _write_synthetic_wav(staged_chunks / "chunk_0000.wav", duration_sec=1.0)
    _write_synthetic_wav(staged_chunks / "chunk_0001.wav", duration_sec=1.0)
    target_chunks = container_dir / "_chunks_Ed_Amit_10-16-2026"
    target_chunks.mkdir(parents=True, exist_ok=True)
    migrate_staged_files(
        chunks_dir=staged_chunks,
        target_chunks_dir=target_chunks,
        metadata_pairs=[],
        is_final=True,
    )
    assert (target_chunks / "chunk_0000.wav").is_file()
    assert (target_chunks / "chunk_0001.wav").is_file()
    assert not (staged_chunks / "chunk_0000.wav").exists()

    # 2. Canonical CLI command contract check (BUG-07: no --mutate-graph, includes --time)
    cmd = build_canonical_ingest_command(
        input_path=wav_path,
        project="NEE",
        meeting="Ed_Amit",
        date="10-16-2026",
        attendees=["Amit Ayer (Managing Director, PartnerAdvisory)", "Eduardo More (Director, NEE)"],
        reviewer="operator",
        filename_slug="Ed_Amit",
        worktree_dir=worktree_dir,
        time="10:34 AM - 11:07 AM ET",
    )
    assert "--mutate-graph" not in cmd
    assert "--time" in cmd
    assert "10:34 AM - 11:07 AM ET" in cmd

    # 3. Write stitched meeting WAV, acoustic receipt, speaker hints queue, and session manifest
    audio_sha = _write_synthetic_wav(wav_path, duration_sec=2.0)
    acoustic_receipt = container_dir / "00_10-16-2026_1034am_et_ed_amit_acoustic_evidence_receipt.json"
    acoustic_receipt.write_text(
        json.dumps({
            "status": "VERIFIED",
            "clusters": [
                {"cluster_id": "SPEAKER_00", "turn_count": 212, "duration_sec": 980.0},
                {"cluster_id": "SPEAKER_01", "turn_count": 128, "duration_sec": 620.0},
            ],
        }),
        encoding="utf-8",
    )
    hints_file = container_dir / "speaker_hints_queue.json"
    hints_file.write_text(
        json.dumps([
            {
                "event_id": "evt_1",
                "speaker": "Eduardo More",
                "quote": "93 reviewed, 62 rejected",
                "timestamp_sec": 12.0,
                "status": "QUEUED",
            }
        ]),
        encoding="utf-8",
    )
    manifest_file = container_dir / "00_10-16-2026_1034am_et_ed_amit_manifest.json"
    manifest_file.write_text(
        json.dumps({
            "session_id": "sess_ed_amit",
            "project": "NEE",
            "meeting": "Ed_Amit",
            "started_at": "2026-10-16T10:34:00-04:00",
            "stopped_at": "2026-10-16T11:07:00-04:00",
            "attendees": [
                {"name": "Amit Ayer", "role": "Managing Director", "org": "PartnerAdvisory"},
                {"name": "Eduardo More", "role": "Director", "org": "NEE"},
            ],
        }),
        encoding="utf-8",
    )

    # 4. Seed master ASR cache (.raw_asr.json) and verify unified lookup (BUG-13)
    segments_jsonl = container_dir / "00_10-16-2026_1034am_et_ed_amit_segments.jsonl"
    raw_segments = [
        {"start": 0.0, "end": 4.0, "text": "Good morning Eduardo, let us review the Maximo status.", "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 9.0, "text": "Out of the total, 93 reviewed, 62 rejected in the queue.", "speaker": "SPEAKER_01"},
        {"start": 9.0, "end": 14.0, "text": "Got it, we will populate the Maximo workbook today.", "speaker": "SPEAKER_00"},
    ]
    segments_jsonl.write_text(
        "\n".join(json.dumps(s) for s in raw_segments) + "\n",
        encoding="utf-8",
    )
    cache_root = worktree_dir / "output" / "transcript_cache"
    stub_recorder = SimpleNamespace(
        segments_jsonl=segments_jsonl,
        output_dir=container_dir,
        clean_slug="Ed_Amit",
        cache_dir=cache_root,
        transcript_cache_dir=cache_root,
        master_wav=wav_path,
        processed_chunks=[Path("c1"), Path("c2")],
        whisper_model="mlx-community/whisper-large-v3-turbo",
        language="auto",
        mock_mode=False,
    )
    stub_receipt = SimpleNamespace(
        master_wav_sha256=audio_sha,
        total_chunks=2,
        duration_s=14.0,
    )
    seeded = seed_master_asr_from_recorder(stub_recorder, stub_receipt)
    assert seeded is True

    cache_root = worktree_dir / "output" / "transcript_cache"
    loaded_asr = load_audio_asr_cache(
        audio_sha256=audio_sha,
        model_name="mlx-community/whisper-large-v3-turbo",
        language=None,
        stem=wav_path.stem,
        cache_dir=cache_root,
    )
    assert loaded_asr is not None
    assert len(loaded_asr) == 3
    assert all(isinstance(s.get("words"), list) and len(s["words"]) > 0 for s in loaded_asr)

    # 5. Hydrate live container artifacts in Stage U0 (BUG-11, BUG-12, BUG-14)
    meta: Dict[str, Any] = {
        "project": f"NEE_E2E_{tmp_path.name}",
        "meeting_name": "Ed_Amit",
        "date": "10-16-2026",
        "output_dir": str(container_dir),
        "attendees": "Amit Ayer, Eduardo More",
    }
    hydrate_live_container_artifacts(meta, input_path=str(wav_path))
    assert meta["time"] == "10:34 AM - 11:07 AM ET"
    assert isinstance(meta["attendees"], list)
    assert meta["attendees"][0]["role"] == "Managing Director"
    assert len(meta["turn_hints"]) == 1
    assert len(meta["acoustic_clusters"]) == 2

    # 6. Format diarized transcript via BaseAudioTranscriber without polluting speaker_map (BUG-15)
    stub = _StubTranscriber(mock=True)
    diarized_text = stub._format_diarized_json(raw_segments, metadata=meta)
    assert "**Speaker 1** [00:00:00]:" in diarized_text
    assert "**Speaker 2** [00:00:04]:" in diarized_text
    assert meta.get("speaker_map") in (None, {})
    assert meta["cluster_label_map"] == {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Speaker 2"}

    # Verify Stage U0 _detect_diarization_status defers to Stage C0-PA instead of raising UnmappedSpeakerError
    u0_status = U0IngestionBoundary._detect_diarization_status(diarized_text, meta)
    assert u0_status == "DIARIZED_ACOUSTIC"
    meta["diarization_status"] = u0_status

    # 7. Execute Stage C0-PA attribution handoff (BUG-16, BUG-17, BUG-18)
    _, resolved_tx = execute_c0_pa(
        pipeline=_make_stub_pipeline(),
        modality=CanonicalModality.AUDIO,
        source_text=diarized_text,
        input_hash=audio_sha,
        meta=meta,
        presentation_path=None,
        out_p=container_dir,
        tracker=_NoOpTracker(),
    )
    assert meta["speaker_map"]["Speaker 2"] == "Eduardo More"
    assert meta["speaker_map"]["Speaker 1"] == "Amit Ayer"
    assert "**Eduardo More** [00:00:04]:" in resolved_tx
    assert "**Amit Ayer** [00:00:00]:" in resolved_tx
    assert "**Eduardo More:**" not in resolved_tx
    assert "**Amit Ayer:**" not in resolved_tx


def test_interactive_hitl_diarization_handoff_when_margin_ambiguous_e2e(tmp_path: Path) -> None:
    """
    End-to-end verification that when speaker attribution margin is ambiguous (<= 30%)
    and no hints resolve the speakers:
    1. Without an interactive_resolver, execute_c0_pa raises HITLRequiredError populated with
       2-turn review_contexts (BEFORE 2, BEFORE 1, TARGET, AFTER 1, AFTER 2) and options.
    2. With an interactive_resolver in metadata, execute_c0_pa invokes the interactive HITL
       callback and produces a fully attributed transcript.
    """
    ambiguous_tx = "\n\n".join([
        "**Speaker 1** [00:00:01]: Opening turn one.",
        "**Speaker 1** [00:00:05]: Opening turn two.",
        "**Speaker 2** [00:00:10]: Target turn needing attribution.",
        "**Speaker 1** [00:00:15]: Following turn one.",
        "**Speaker 1** [00:00:20]: Following turn two.",
    ])
    base_meta: Dict[str, Any] = {
        "project": f"NEE_HITL_{tmp_path.name}",
        "meeting_name": "Architecture Sync",
        "date": "10-16-2026",
        "diarization_status": "DIARIZED_ACOUSTIC",
        "has_acoustic_diarization": True,
        "hitl_decision_store": HITLDecisionStore(ledger_path=tmp_path / "hitl_isolated.json"),
        "attendees": [
            {"name": "Amit Ayer", "role": "MD", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
    }

    # Case A: No interactive_resolver -> raises HITLRequiredError with 2-turn review context
    with pytest.raises(HITLRequiredError) as exc_info:
        execute_c0_pa(
            pipeline=_make_stub_pipeline(),
            modality=CanonicalModality.AUDIO,
            source_text=ambiguous_tx,
            input_hash="hash_ambiguous_a",
            meta=dict(base_meta),
            presentation_path=None,
            out_p=tmp_path,
            tracker=_NoOpTracker(),
        )
    err = exc_info.value
    assert len(err.options) == 2
    assert len(err.review_contexts) >= 1
    assert err.context_digest
    validate_visible_speaker_context(err.prompt)

    # Case B: Interactive resolver provided -> resolves via HITL callback and completes Stage C0-PA
    prompts_seen: List[str] = []

    def _hitl_callback(prompt_text: str, options: List[str]) -> str:
        validate_visible_speaker_context(prompt_text)
        prompts_seen.append(prompt_text)
        return options[0]

    meta_with_resolver = {**base_meta, "interactive_resolver": _hitl_callback}
    _, resolved_tx = execute_c0_pa(
        pipeline=_make_stub_pipeline(),
        modality=CanonicalModality.AUDIO,
        source_text=ambiguous_tx,
        input_hash="hash_ambiguous_b",
        meta=meta_with_resolver,
        presentation_path=None,
        out_p=tmp_path,
        tracker=_NoOpTracker(),
    )
    assert len(prompts_seen) >= 1
    assert set(meta_with_resolver["speaker_map"].values()) == {"Amit Ayer", "Eduardo More"}
    assert "**Amit Ayer**" in resolved_tx
    assert "**Eduardo More**" in resolved_tx
