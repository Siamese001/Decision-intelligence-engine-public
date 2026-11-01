"""Wave 1 tests: Live Recorder In-Flight Sanitization, Post-Write Fault Isolation & Retroactive Voice Propagation."""
from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.cli.copilot_session import _read_turns_from_jsonl
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader, _parse_markdown_turns
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest, SessionRegistry


def _write_test_wav(path: Path, duration_sec: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x10\x00" * int(16000 * duration_sec))


def test_recorder_inflight_prompt_echo_rejected_before_commit(tmp_path: Path, monkeypatch) -> None:
    """Prompt echoes and high no_speech_prob hallucinations must be dropped in process_chunk before writing to segments.jsonl."""
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / "locks"))
    out_dir = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Test_Meeting"
    rec = LiveMeetingRecorder(
        project_name="partner_alliances",
        meeting_name="Test Meeting",
        meeting_date="10-27-2026",
        attendees=["Amit", "Traci"],
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        chunk0 = rec.chunks_dir / "chunk_00000.wav"
        _write_test_wav(chunk0)

        rec._transcribe_chunk = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "Meeting context: Test Meeting. Attendees: Amit, Traci. Technical and domain vocabulary: Apex Global Enterprises.",
                [{"text": "Meeting context: Test Meeting.", "no_speech_prob": 0.1, "avg_logprob": -0.2}],
            )
        )
        res_echo = rec.process_chunk(chunk0, 0)
        assert res_echo is None
        assert not rec.segments_jsonl.exists() or rec.segments_jsonl.read_text(encoding="utf-8").strip() == ""

        rec._transcribe_chunk = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "Thank you for watching.",
                [{"text": "Thank you for watching.", "no_speech_prob": 0.92, "avg_logprob": -0.5}],
            )
        )
        res_halluc = rec.process_chunk(chunk0, 0)
        assert res_halluc is None
        assert not rec.segments_jsonl.exists() or rec.segments_jsonl.read_text(encoding="utf-8").strip() == ""
    finally:
        rec._release_slug_lock()


def test_recorder_commit_segment_fault_isolation_prevents_retry_duplication(tmp_path: Path, monkeypatch) -> None:
    """An exception inside speaker_hint_queue.reconcile_pending_hints must not fail process_chunk after segments.jsonl append."""
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / "locks"))
    out_dir = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Fault_Isolation"
    rec = LiveMeetingRecorder(
        project_name="partner_alliances",
        meeting_name="Fault Isolation",
        meeting_date="10-27-2026",
        attendees=["Amit", "Traci"],
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        chunk0 = rec.chunks_dir / "chunk_00000.wav"
        _write_test_wav(chunk0)

        rec._transcribe_chunk = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "We need a Technical Account Manager to backfill the open role.",
                [{"text": "We need a Technical Account Manager to backfill the open role.", "no_speech_prob": 0.05, "avg_logprob": -0.1}],
            )
        )
        rec.speaker_hint_queue = MagicMock()
        rec.speaker_hint_queue.reconcile_pending_hints.side_effect = RuntimeError("simulated hint lock contention")

        res = rec.process_chunk(chunk0, 0)
        assert res is not None
        lines = [ln for ln in rec.segments_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 1
    finally:
        rec._release_slug_lock()


def test_retroactive_voice_id_propagation_across_live_buffer_and_session(tmp_path: Path) -> None:
    """When voice_1 is bound via speaker hint or voice_registry.json, all earlier turns in voice_1 resolve to the bound speaker."""
    out_dir = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Voice_Cluster_Test"
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    seg_path = cache_dir / "segments.jsonl"

    segments = [
        {"chunk_idx": 0, "timestamp": "00:00:00", "start_sec": 0.0, "speaker": "Speaker", "voice_id": "voice_1", "text": "Early turn before hint was submitted."},
        {"chunk_idx": 1, "timestamp": "00:00:15", "start_sec": 15.0, "speaker": "Speaker", "voice_id": "voice_1", "text": "We should hire a Solution Architect for the backfill."},
        {"chunk_idx": 2, "timestamp": "00:00:30", "start_sec": 30.0, "speaker": "Amit", "voice_id": "voice_host", "text": "Got it, thank you for clarifying."},
    ]
    seg_path.write_text("\n".join(json.dumps(s) for s in segments) + "\n", encoding="utf-8")

    hints = [
        {"hint_id": "h1", "speaker_name": "Traci", "quote": "hire a Solution Architect for the backfill", "status": "QUEUED"}
    ]
    (cache_dir / "speaker_hints_queue.json").write_text(json.dumps(hints), encoding="utf-8")

    manifest = SessionManifest(
        session_id="sess_voice_1",
        engine_signature="test_sig",
        parent_pid=1234,
        parent_start_time=1.0,
        engagement_id="project:partner_alliances",
        engagement_name="partner_alliances",
        meeting_slug="Voice_Cluster_Test",
        repo_root=str(tmp_path),
        output_dir=str(out_dir.relative_to(tmp_path)),
        cache_dir=str(cache_dir.relative_to(tmp_path)),
        segments_jsonl=str(seg_path.relative_to(tmp_path)),
        attendees=["Amit", "Traci"],
    )

    reader = LiveBufferReader(tmp_path)
    turns = reader.read_turns(manifest)
    assert len(turns) == 3
    assert turns[0]["speaker"] == "Traci"
    assert turns[1]["speaker"] == "Traci"
    assert turns[2]["speaker"] == "Amit"

    session_turns = _read_turns_from_jsonl(out_dir, attendees=["Amit", "Traci"])
    assert len(session_turns) == 3
    assert session_turns[0]["speaker"] == "Traci"
    assert session_turns[1]["speaker"] == "Traci"


def test_markdown_fallback_filters_prompt_echoes() -> None:
    md = (
        "[00:00:00] Speaker: Meeting context: Google NEE PartnerAdvisory Monthly. Attendees: Amit, Stefanos.\n"
        "[00:00:15] Stefanos: We are moving the Early Access Program to fifty users.\n"
    )
    turns = _parse_markdown_turns(md)
    assert len(turns) == 1
    assert turns[0]["speaker"] == "Stefanos"
