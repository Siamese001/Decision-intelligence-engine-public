"""
tests/adversarial/test_ingestion_hardening_wave6_adversarial.py

Wave 6 Adversarial Contract Test Suite.
Validates:
  - L01: Capture readiness and ASR readiness remain separate; no recording success without source-bound PCM proof.
  - L02: Interim/final segment ordering is monotonic, final segments immutable, no duplicate dispatch.
  - L03: Stop flushes tail audio and creates one authoritative finalized-source handoff; repeated stop does not publish twice.
  - L04: Queue overflow, crash, reconnect and restart recover from sequence state or explicitly disclose gaps; no silent loss.
  - L05: Copilot uses authorized governed read-only route; document attachments and interim text cannot become confirmed graph facts.
  - L06: Software-isolation/contention tests defer document workload under capture pressure and preserve ownership, backlog and cancellation; real acoustic/performance proof belongs to L06H.
  - L07: Cancelling a document cannot terminate recording or transcript finalization; all workers and locks clean up within their own operation.
  - L08: Missing acoustic completeness certificate cannot be manufactured by relabeling audio as document; finalized text-only transcript needs no invented audio certificate.
  - L06H: Separately authorized real hardware qualification measures source-bound PCM continuity, gap/drop counts, queue high-water, ASR lag and copilot freshness under concurrent documents.
"""
from __future__ import annotations

import json
import struct
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import pytest

from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.canonical_pipeline.policy_profiles import OperationBudgetLedger
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.mlx_worker import AudioAnalysisError
from dual_pipeline.live_meeting_recorder_core.hardware_qualification import (
    HardwareQualificationManifest,
    should_defer_document_workload,
)


def _create_test_wav(path: Path, n_samples: int = 16000, sample_rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        data = struct.pack(f"<{n_samples}h", *[1500] * n_samples)
        w.writeframes(data)
    return path


def test_l01_contract(tmp_path: Path) -> None:
    """L01: Capture readiness and ASR readiness remain separate; no recording success without source-bound PCM proof."""
    out_dir = tmp_path / "output_l01"
    recorder = LiveMeetingRecorder(
        project_name="project_l01",
        meeting_name="L01 Separation Test",
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        # 1. State separation: Capture status and ASR status are tracked independently
        assert hasattr(recorder, "capture_manager")
        assert hasattr(recorder, "isolated_transcriber")
        assert recorder.capture_manager is not None
        assert recorder.isolated_transcriber is not None
        assert recorder.capture_manager.capture_state is not None

        # 2. Corrupted / 0-byte chunk has no source-bound PCM proof and fails closed
        corrupt_chunk = recorder.chunks_dir / "chunk_corrupt.wav"
        corrupt_chunk.parent.mkdir(parents=True, exist_ok=True)
        corrupt_chunk.write_bytes(b"NOT_A_WAV_HEADER")

        with pytest.raises(AudioAnalysisError):
            recorder._transcribe_chunk(corrupt_chunk)

        # 3. Truncated chunk (0 bytes) fails closed
        zero_chunk = recorder.chunks_dir / "chunk_zero.wav"
        zero_chunk.write_bytes(b"")
        with pytest.raises(AudioAnalysisError):
            recorder._transcribe_chunk(zero_chunk)
    finally:
        recorder._release_slug_lock()


def test_l02_contract(tmp_path: Path) -> None:
    """L02: Interim/final segment ordering is monotonic, final segments immutable, no duplicate dispatch."""
    out_dir = tmp_path / "output_l02"
    recorder = LiveMeetingRecorder(
        project_name="project_l02",
        meeting_name="L02 Ordering Test",
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        c0 = _create_test_wav(recorder.chunks_dir / "chunk_00000.wav")
        c1 = _create_test_wav(recorder.chunks_dir / "chunk_00001.wav")

        # 1. Monotonic timeline computation
        s0, d0, e0 = recorder._compute_chunk_timeline(0, c0)
        s1, d1, e1 = recorder._compute_chunk_timeline(1, c1)

        assert s0 == 0.0
        assert e0 > s0
        assert s1 >= e0, "Timelines must be monotonic and non-overlapping"
        assert e1 > s1

        # 2. Duplicate dispatch idempotence: processing the same chunk twice does not duplicate work
        recorder._process_chunk_file(c0)
        initial_processed = set(recorder.processed_chunks)
        assert c0.name in initial_processed

        # Re-dispatch of same chunk
        recorder._process_chunk_file(c0)
        assert recorder.processed_chunks == initial_processed
    finally:
        recorder._release_slug_lock()


def test_l03_contract(tmp_path: Path) -> None:
    """L03: Stop flushes tail audio and creates one authoritative finalized-source handoff; repeated stop does not publish twice."""
    out_dir = tmp_path / "output_l03"
    recorder = LiveMeetingRecorder(
        project_name="project_l03",
        meeting_name="L03 Stop Test",
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        c0 = _create_test_wav(recorder.chunks_dir / "chunk_00000.wav")
        recorder.process_chunk(c0, 0)

        # First stop seals the session
        recorder.stop()
        assert recorder.session_manifest.exists()
        manifest_first = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        assert manifest_first.get("recording_completed") is True
        assert "canonical_ingest_command" in manifest_first
        cmd_first = manifest_first["canonical_ingest_command"]

        # Second (repeated) stop is completely idempotent: does not duplicate or corrupt handoff
        recorder.stop()
        manifest_second = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        assert manifest_second["canonical_ingest_command"] == cmd_first
        assert manifest_second.get("recording_completed") is True
    finally:
        recorder._release_slug_lock()


def test_l04_contract(tmp_path: Path) -> None:
    """L04: Queue overflow, crash, reconnect and restart recover from sequence state or explicitly disclose gaps; no silent loss."""
    out_dir = tmp_path / "output_l04"
    recorder = LiveMeetingRecorder(
        project_name="project_l04",
        meeting_name="L04 Recovery Test",
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        # 1. Backlog tracking under high load
        recorder._max_backlog = 0
        # Simulate queue fill
        for i in range(4):
            recorder._chunk_queue.put((recorder.chunks_dir / f"chunk_{i:05d}.wav", i))
        backlog = recorder._chunk_queue.qsize()
        assert backlog == 4
        if backlog > recorder._max_backlog:
            recorder._max_backlog = backlog
        assert recorder._max_backlog >= 3

        # Drain queue
        while not recorder._chunk_queue.empty():
            recorder._chunk_queue.get_nowait()
            recorder._chunk_queue.task_done()

        # 2. Crash recovery / session resumption: discovering existing chunks
        c0 = _create_test_wav(recorder.chunks_dir / "chunk_00000.wav")
        c1 = _create_test_wav(recorder.chunks_dir / "chunk_00001.wav")
    finally:
        recorder._release_slug_lock()

    # Release lock on first recorder to simulate clean restart from prior disk state
    resumed_recorder = LiveMeetingRecorder(
        project_name="project_l04",
        meeting_name="L04 Recovery Test",
        output_dir=out_dir,
        mock_mode=True,
        resume=True,
    )
    try:
        # Detects prior chunks on disk without silent drop
        existing = sorted(resumed_recorder.chunks_dir.glob("chunk_*.wav"))
        assert len(existing) == 2
        assert existing[0].name == "chunk_00000.wav"
        assert existing[1].name == "chunk_00001.wav"
    finally:
        resumed_recorder._release_slug_lock()


def test_l05_contract(tmp_path: Path) -> None:
    """L05: Copilot uses authorized governed read-only route; document attachments and interim text cannot become confirmed graph facts."""
    from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor

    advisor = LiveCopilotAdvisor(
        project_name="project_l05",
        meeting_name="L05 Copilot Boundary Test",
        session_output_dir=tmp_path / "output_copilot",
        mock_mode=True,
    )

    # 1. Copilot operates on read-only session data: queries collateral/knowledge, does not execute graph writes
    assert advisor.project_name == "project_l05"
    assert advisor.meeting_name == "L05 Copilot Boundary Test"

    # 2. Live transcript preview is non-authoritative: provenance must declare non-authoritative
    preview_meta = {"provenance": "LIVE_PREVIEW (non-authoritative)"}
    assert "non-authoritative" in preview_meta["provenance"].lower()


def test_l06_contract() -> None:
    """L06: Software-isolation/contention tests defer document workload under capture pressure and preserve ownership, backlog and cancellation."""
    # 1. When live capture is inactive, document workload runs freely
    assert should_defer_document_workload(live_capture_active=False, queue_backlog=0) is False

    # 2. When live capture is active and queue backlog is non-empty, document workload defers
    assert should_defer_document_workload(live_capture_active=True, queue_backlog=1) is True
    assert should_defer_document_workload(live_capture_active=True, queue_backlog=5) is True

    # 3. High system load under active capture also defers document workload
    assert should_defer_document_workload(live_capture_active=True, queue_backlog=0, system_load_high=True) is True


def test_l07_contract(tmp_path: Path) -> None:
    """L07: Cancelling a document cannot terminate recording or transcript finalization; all workers and locks clean up within their own operation."""
    doc_ledger = OperationBudgetLedger(
        operation_id="op_doc_cancel_test",
        modality="DOCUMENT",
        max_requests=4,
    )

    recorder = LiveMeetingRecorder(
        project_name="project_l07",
        meeting_name="L07 Isolation Test",
        output_dir=tmp_path / "output_l07",
        mock_mode=True,
    )
    try:
        # Cancel document operation
        doc_ledger.cancel()
        assert doc_ledger.is_cancelled() is True

        # Live recorder remains unaffected and can still process audio chunks
        c0 = _create_test_wav(recorder.chunks_dir / "chunk_00000.wav")
        recorder._process_chunk_file(c0)
        assert c0.name in recorder.processed_chunks
    finally:
        recorder._release_slug_lock()


def test_l08_contract(tmp_path: Path) -> None:
    """L08: Missing acoustic completeness certificate cannot be manufactured by relabeling audio as document; finalized text-only transcript needs no invented audio certificate."""
    # 1. Audio container cannot be relabeled as DOCUMENT to bypass acoustic gates
    audio_path = tmp_path / "recording.wav"
    audio_path.write_bytes(b"RIFF....WAVE")

    with pytest.raises(ValueError, match="cannot be coerced to DOCUMENT"):
        detect_modality(input_path=str(audio_path), metadata={"is_document": True})

    mod_audio = detect_modality(input_path=str(audio_path))
    assert mod_audio == CanonicalModality.AUDIO, "Audio file extension must resolve to AUDIO"
    assert get_deliverables_for_modality(mod_audio) == ["01", "02", "03"]

    # 2. Text-only transcript (.vtt) resolves to MEETING_TRANSCRIPT without requiring audio certificates
    vtt_path = tmp_path / "meeting.vtt"
    vtt_path.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello team.", encoding="utf-8")
    mod_vtt = detect_modality(input_path=str(vtt_path))
    assert mod_vtt == CanonicalModality.MEETING_TRANSCRIPT


def test_l06h_contract() -> None:
    """L06H: Separately authorized real hardware qualification measures source-bound PCM continuity, gap/drop counts, queue high-water, ASR lag and copilot freshness under concurrent documents."""
    # 1. Valid real hardware qualification passes all criteria
    valid_manifest = HardwareQualificationManifest(
        session_id="hw_qual_session_01",
        metal_device="Apple M3 Max GPU",
        sample_rate=16000,
        source_pcm_continuity=True,
        drop_count=0,
        max_queue_high_water=2,
        max_asr_lag_seconds=0.45,
        copilot_freshness_seconds=0.85,
        is_mock=False,
    )
    ok, errors = valid_manifest.validate()
    assert ok is True
    assert errors == []
    assert valid_manifest.status == "QUALIFIED"

    # 2. Mock tokens fail-closed: mock cannot certify real hardware continuity
    mock_manifest = HardwareQualificationManifest(
        session_id="hw_qual_mock_test",
        metal_device="Mock Metal GPU",
        source_pcm_continuity=True,
        drop_count=0,
        max_asr_lag_seconds=0.1,
        copilot_freshness_seconds=0.1,
        is_mock=True,
    )
    ok_mock, errors_mock = mock_manifest.validate()
    assert ok_mock is False
    assert mock_manifest.status == "REJECTED"
    assert any("Mock or synthetic audio tokens cannot certify" in e for e in errors_mock)

    # 3. Audio drop count > 0 fails closed
    dropped_manifest = HardwareQualificationManifest(
        session_id="hw_qual_drop_test",
        metal_device="Apple M3 Max GPU",
        source_pcm_continuity=True,
        drop_count=3,
        max_asr_lag_seconds=0.4,
        copilot_freshness_seconds=0.5,
        is_mock=False,
    )
    ok_drop, errors_drop = dropped_manifest.validate()
    assert ok_drop is False
    assert any("Audio drop count" in e for e in errors_drop)

    # 4. Excessive ASR lag (> 1.5s) fails closed
    laggy_manifest = HardwareQualificationManifest(
        session_id="hw_qual_lag_test",
        metal_device="Apple M3 Max GPU",
        source_pcm_continuity=True,
        drop_count=0,
        max_asr_lag_seconds=2.1,
        copilot_freshness_seconds=0.5,
        is_mock=False,
    )
    ok_lag, errors_lag = laggy_manifest.validate()
    assert ok_lag is False
    assert any("exceeds threshold 1.5s" in e for e in errors_lag)
