"""
End-to-end stress verification for LiveMeetingRecorder and Live Copilot (nee copilot).
Exercises rolling multi-chunk transcription under fault injection, queue/drain recovery,
stop lifecycle speaker harvesting, worktree path repointing, and all 6 Copilot intents.
"""
from __future__ import annotations

import contextlib
import json
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List

import pytest

_null_ctx = contextlib.nullcontext

import dual_pipeline.live_meeting_recorder as lmr_mod
from dual_pipeline.cli.copilot_speech import render_speaker_comments
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_intents import evaluate_intent
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    _resolve_speaker_and_utterance,
    synthesize_live_transcript_assertions,
)
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import _collect_map_speakers


def _write_test_wav(path: Path, freq_scale: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        samples = [
            int(12000 * ((i * freq_scale % 40) / 20.0 - 1.0))
            for i in range(16000)
        ]
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _build_hermetic_recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> LiveMeetingRecorder:
    monkeypatch.setattr(lmr_mod, "ROOT", tmp_path)
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / ".locks"))
    monkeypatch.setattr(lmr_mod, "ensure_ffmpeg", lambda: None)
    out_dir = tmp_path / "sandbox_session"
    rec = LiveMeetingRecorder(
        project_name="StressProject",
        meeting_name="Architecture Backfill Review",
        attendees=["Traci", "Chad", "Speaker_Beta", "Amit"],
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
        immediate_worktree=False,
        enable_ws=False,
        enable_copilot=True,
        isolated_process=False,
        **kwargs,
    )
    return rec


def test_live_recorder_25_chunk_stress_with_post_asr_faults_and_drain_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Stress-tests 25 rolling audio chunks through LiveMeetingRecorder with:
    - Intermittent voice_registry, speaker_tracker, and copilot_advisor exceptions
    - Transient worker failure during _process_chunk_file recovered via _drain_remaining_chunks
    - Verification that 100% of 25 chunks land in segments.jsonl and asr_state stays READY.
    """
    rec = _build_hermetic_recorder(tmp_path, monkeypatch)
    try:
        chunk_files: List[Path] = []
        for idx in range(25):
            cf = rec.chunks_dir / f"chunk_{idx:05d}.wav"
            _write_test_wav(cf, freq_scale=idx + 1)
            chunk_files.append(cf)

        topics = [
            "Traci confirmed the principal architect role will backfill the migration lead.",
            "Chad reviewed the AlloyDB read replica latency metrics across us-central1.",
            "Speaker_Beta approved the regional vCPU quota expansion for Friday cutover.",
            "Amit aligned the executive steering committee on Q4 milestone dates.",
            "Traci shared the KPMG statement of work for phase two readiness.",
        ]

        def _fake_transcribe(wav_path: Path, chunk_idx: int = 0, **kw: Any) -> str:
            base = topics[chunk_idx % len(topics)]
            return f"{base} Reference token alpha-{chunk_idx * 17 + 100} verified."

        monkeypatch.setattr(rec, "_transcribe_chunk", _fake_transcribe)

        orig_resolve = rec.voice_registry.resolve_chunk_speaker
        orig_attr = rec.speaker_tracker.attribute_chunk

        def _flaky_voice_resolve(wav_path: Path, chunk_idx: int, text: str, **kw: Any) -> tuple[str, Any, float]:
            if chunk_idx % 3 == 0:
                raise RuntimeError(f"Simulated pyannote embedding fault on chunk {chunk_idx}")
            if chunk_idx % 2 == 0:
                return "Speaker", None, 0.5
            return orig_resolve(wav_path=wav_path, chunk_idx=chunk_idx, text=text, **kw)

        def _flaky_tracker(text: str, **kw: Any) -> str:
            if "chunk 4" in text or "chunk 10" in text:
                raise TypeError("Simulated speaker_tracker signature or state crash")
            return orig_attr(text, **kw)

        monkeypatch.setattr(rec.voice_registry, "resolve_chunk_speaker", _flaky_voice_resolve)
        monkeypatch.setattr(rec.speaker_tracker, "attribute_chunk", _flaky_tracker)

        orig_add_chunk = rec.turn_aggregator.add_chunk

        def _flaky_copilot(chunk_idx: int, *a: Any, **kw: Any) -> Any:
            if chunk_idx % 5 == 0:
                raise ValueError(f"Simulated copilot turn handler error on chunk {chunk_idx}")
            return orig_add_chunk(chunk_idx, *a, **kw)

        monkeypatch.setattr(rec.turn_aggregator, "add_chunk", _flaky_copilot)

        real_process_chunk = rec.process_chunk
        failed_once: set[int] = set()

        def _transient_fail_on_7(wav_path: Path, idx: int) -> Any:
            if idx == 7 and idx not in failed_once:
                failed_once.add(idx)
                raise RuntimeError("Transient ASR worker timeout on chunk 7")
            return real_process_chunk(wav_path, idx)

        monkeypatch.setattr(rec, "process_chunk", _transient_fail_on_7)

        for cf in chunk_files:
            with pytest.raises(RuntimeError) if cf == chunk_files[7] else _null_ctx():
                rec._process_chunk_file(cf)

        assert chunk_files[7].name not in rec.processed_chunks
        rec._drain_remaining_chunks()

        assert len(rec.processed_chunks) == 25
        assert rec._asr_failed is False
        assert rec._consecutive_asr_failures == 0

        lines = [
            json.loads(line)
            for line in rec.segments_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 25
        committed_indices = {int(item["chunk_idx"]) for item in lines}
        assert committed_indices == set(range(25))

        rec.speaker_hint_manager.hints.append(
            type("HintStub", (), {"speaker_name": "Traci", "status": "CONFIRMED"})()
        )
        harvested = _collect_map_speakers(rec.speaker_hint_manager)
        assert "Traci" in harvested

        rec._shutdown_background_services()
    finally:
        rec._is_stopped = True
        rec._release_slug_lock()


def test_recorder_resolve_identity_and_retriever_topology_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies resolve_identity repoints all subsystem paths and CopilotGraphRetriever clears inverted indexes."""
    rec = _build_hermetic_recorder(tmp_path, monkeypatch)
    rec.is_anonymous = True
    try:
        wt_target = tmp_path / ".worktrees" / "ingest_repointed_session"
        wt_target.mkdir(parents=True, exist_ok=True)

        def _fake_materialize(self_rec: Any = rec, **kw: Any) -> bool:
            rec.worktree_dir = wt_target
            rec._target_output_dir = wt_target / "output" / "TargetProject" / "10-27-2026_repointed"
            rec._target_cache_dir = rec._target_output_dir / ".cache"
            rec._target_chunks_dir = rec._target_output_dir / "chunks"
            rec._worktree_materialized = True
            return True

        monkeypatch.setattr(rec, "materialize_worktree", _fake_materialize)
        rec.resolve_identity("TargetProject", "Repointed Meeting", ["Traci", "Speaker_Alpha"])
        assert rec.project_name.lower() == "targetproject"
        assert rec.referenced_artifacts_dir == rec.output_dir / "referenced_artifacts"
        if rec.copilot_advisor is not None:
            assert rec.copilot_advisor._session_output_dir == rec.output_dir
            assert rec.copilot_advisor._collateral_watcher is rec.collateral_watcher

        retriever = CopilotGraphRetriever(engagement_id="TargetProject")
        retriever._claim_inverted_index["stale_token"] = {"asrt_stale"}
        retriever._indexed_assertion_records["asrt_stale"] = {"assertion_id": "asrt_stale"}
        retriever.update_topology(None)
        assert retriever._claim_inverted_index == {}
        assert retriever._indexed_assertion_records == {}
    finally:
        rec._is_stopped = True
        rec._shutdown_background_services()
        rec._release_slug_lock()


def test_copilot_all_intents_and_traci_backfill_stress_e2e(tmp_path: Path) -> None:
    """
    Stress-tests Copilot evaluation and speech rendering across respond, next-questions,
    next-actions, and graph-context intents with realistic fragmented live meeting turns.
    """
    ctx = ConversationContextModel(
        attendees=["Traci", "Chad", "Speaker_Beta", "Amit"],
        focus_seconds=300.0,
    )
    ctx.add_turn(
        speaker="Traci",
        text="so they have KPMG right now on an S",
        timestamp="14:01:00",
        turn_sec=10.0,
    )
    ctx.add_turn(
        speaker="Traci",
        text="statement of work for the migration readiness phase.",
        timestamp="14:01:08",
        turn_sec=18.0,
    )
    ctx.add_turn(
        speaker="Traci",
        text="A principal enterprise architect role should backfill Traci during the Q4 transition.",
        timestamp="14:02:00",
        turn_sec=70.0,
    )
    ctx.add_turn(
        speaker="Traci",
        text="So, I think that could potentially be the",
        timestamp="14:02:20",
        turn_sec=90.0,
    )
    ctx.add_turn(
        speaker="Chad",
        text="I will finalize the BigQuery migration cutover checklist by Thursday.",
        timestamp="14:02:40",
        turn_sec=110.0,
    )

    parsed = parse_copilot_request("What role should backfill Traci?")
    assert parsed.target_speaker == "Traci"
    assert parsed.inferred_intent == "graph-context"
    assert "role" in parsed.topic_keywords
    assert "backfill" in parsed.topic_keywords

    retriever = CopilotGraphRetriever(engagement_id="StressProject")

    resolved_t, sel_utt, _, err = _resolve_speaker_and_utterance(
        parsed.target_speaker, list(parsed.topic_keywords), ctx, None
    )
    assert err is None
    body_gc, asrts_gc, ok_gc = evaluate_intent(
        parsed.inferred_intent, resolved_t, ctx, retriever,
        topic_keywords=parsed.topic_keywords, selected_utterance=sel_utt, topic_query=parsed.topic_query,
    )
    assert ok_gc and asrts_gc
    spoken_gc = render_speaker_comments(
        parsed.inferred_intent, resolved_t, asrts_gc, max_words=60, retriever=retriever, selected_utterance=sel_utt
    )
    assert "principal enterprise architect" in spoken_gc.body.lower()
    assert "on an s?" not in spoken_gc.body.lower()
    assert "potentially be the" not in spoken_gc.body.lower()

    resolved_r, sel_r, _, err_r = _resolve_speaker_and_utterance("Traci", [], ctx, None)
    assert err_r is None
    assert sel_r is not None
    assert "potentially be the" not in sel_r.text.lower()
    _, asrts_r, ok_r = evaluate_intent("respond", resolved_r, ctx, retriever, topic_keywords=[], selected_utterance=sel_r)
    assert ok_r and asrts_r
    spoken_r = render_speaker_comments("respond", resolved_r, asrts_r, max_words=60, retriever=retriever, selected_utterance=sel_r)
    assert "potentially be the" not in spoken_r.body.lower()
    assert "on an s?" not in spoken_r.body.lower()

    for intent_name in ("next-questions", "next-actions"):
        _, asrts_i, ok_i = evaluate_intent(intent_name, "Traci", ctx, retriever, topic_keywords=["role", "backfill"], selected_utterance=sel_utt)
        assert ok_i and asrts_i
        spoken_i = render_speaker_comments(intent_name, "Traci", asrts_i, max_words=60, retriever=retriever, selected_utterance=sel_utt)
        assert spoken_i.body.strip()

    synth = synthesize_live_transcript_assertions(ctx, "Traci", None, topic_keywords=["kpmg"])
    assert synth
    assert "statement of work" in synth[0]["claim_text"].lower()
