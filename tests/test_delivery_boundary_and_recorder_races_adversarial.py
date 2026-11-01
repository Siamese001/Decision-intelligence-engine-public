"""Adversarial and edge-case tests for Wave 4 (CAT-3 P0 & P1).

Covers mandatory edge-case categories:
1. Concurrency & race (parallel _commit_segment_event and apply_speaker_hint under _transcript_io_lock)
2. Boundary payload (zero audible chunks inactivity timeout, corrupt 0-byte prior chunk files)
3. Invariant bypass attempt (stale AudioStrategyProfileCache when headphones/mute state changes, severity ordering in _audit_all_claims)
4. Exact production regression (INACCESSIBLE_AUDIENCE followed by UNSUPPORTED_CLAIM preserving highest severity)
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.grounding.delivery_boundary import _audit_all_claims
from dual_pipeline.grounding.eligibility import EligibilityDecision
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioCaptureUnprovenError,
    AudioStrategyProfileCache,
    LiveAudioCaptureManager,
)


def test_strategy_cache_invalidates_when_headphone_or_mute_state_changes(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt — cached strategy must not bypass live headphone/mute state changes."""
    AudioStrategyProfileCache.clear()
    AudioStrategyProfileCache.store_fast("auto", ":0", "microphone", None, ":0", False, False)

    mgr = LiveAudioCaptureManager(
        chunks_dir=tmp_path / "chunks",
        master_wav=tmp_path / "master.wav",
        source_mode="auto",
        device_index=":0",
        mock_mode=False,
        skip_device_discovery=True,
    )
    # When headphones are connected and SCK/loopback are unavailable, must NOT return cached 'microphone'
    with patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=True), \
         patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
         patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
         patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=False), \
         patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=None):
        with pytest.raises(AudioCaptureUnprovenError):
            mgr.resolve_source_strategy()


def test_inactivity_timeout_fires_with_zero_audible_chunks_and_corrupt_chunk_bounded(tmp_path: Path) -> None:
    """Category: Boundary payload — inactivity timeout fires when total_audible_chunks == 0, and corrupt chunks do not loop forever."""
    rec = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    rec.running = True
    rec.mock_mode = True
    rec.max_duration_minutes = None
    rec.inactivity_timeout_seconds = 0.01
    rec.total_audible_chunks = 0
    rec.last_audible_monotonic = time.monotonic() - 1.0
    rec.auto_stopped_reason = None
    rec.capture_manager = SimpleNamespace(watchdog_exception=None, _ffmpeg_proc=None)
    rec.collateral_watcher = None
    rec.chat_collateral_bridge = None
    rec._init_transcript_file = MagicMock()
    rec._start_asr_worker = MagicMock()
    rec.start_ffmpeg = MagicMock()
    rec._poll_and_process_chunks = MagicMock()
    rec.stop = MagicMock()

    rec.run(max_iterations=5)
    assert rec.auto_stopped_reason is not None
    assert "INACTIVITY_TIMEOUT" in rec.auto_stopped_reason

    # Corrupt chunk file is added to processed_chunks after retry limit (2 failures)
    rec.processed_chunks = set()
    rec._chunk_failures = {}
    rec._consecutive_asr_failures = 0
    rec._asr_failed = False
    rec.chunks_dir = tmp_path
    corrupt_wav = tmp_path / "chunk_00001.wav"
    corrupt_wav.write_bytes(b"NOT_A_WAV")

    rec._process_chunk_file(corrupt_wav)
    # First failure discards from processed_chunks to allow 1 retry
    assert corrupt_wav.name not in rec.processed_chunks
    rec._process_chunk_file(corrupt_wav)
    # Second failure keeps it in processed_chunks permanently
    assert corrupt_wav.name in rec.processed_chunks


def test_audit_all_claims_preserves_highest_severity_rejection_code() -> None:
    """Category: Exact production regression — INACCESSIBLE_AUDIENCE must not be overwritten by trailing UNSUPPORTED_CLAIM."""
    c1 = SimpleNamespace(claim_id="c1", subject="Alice", predicate="SALARY", object="Restricted internal note")
    c2 = SimpleNamespace(claim_id="c2", subject="Bob", predicate="ROLE", object="Unverified role claim")
    manifest = SimpleNamespace(claims=[c1, c2])
    snapshot = MagicMock()

    res_audience = SimpleNamespace(
        is_eligible=False,
        decision=EligibilityDecision.REJECTED_AUDIENCE,
        rejection_reasons=["Audience mismatch"],
    )
    res_unsupported = SimpleNamespace(
        is_eligible=False,
        decision="REJECTED_UNSUPPORTED",
        rejection_reasons=["Unsupported claim"],
    )
    with patch(
        "dual_pipeline.grounding.delivery_boundary.AssertionEligibilityAuditor.audit_claim",
        side_effect=[res_audience, res_unsupported],
    ):
        supported, contra, code, reasons = _audit_all_claims(manifest, snapshot, audience="CLIENT_SAFE")
    assert supported == 0
    assert code == "INACCESSIBLE_AUDIENCE"
    assert len(reasons) == 2


def test_concurrent_commit_segment_and_apply_speaker_hint_serialized(tmp_path: Path) -> None:
    """Category: Concurrency & race — concurrent segment commits and hint applications do not corrupt JSONL."""
    rec = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    rec._transcript_io_lock = threading.RLock()
    rec.segment_seconds = 15
    rec.clean_slug = "race_slug"
    rec.segments_jsonl = tmp_path / "segments.jsonl"
    rec.transcript_md = tmp_path / "transcript.md"
    rec.transcript_md.write_text("# Live Transcript\n\n", encoding="utf-8")
    rec.voice_registry = None
    rec.speaker_hint_queue = None

    class DummyHintManager:
        def apply_hint(self, **kwargs):
            if rec.segments_jsonl.exists():
                lines = [ln for ln in rec.segments_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
                rec.segments_jsonl.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            return SimpleNamespace(status="PASS", voice_id=None)

    rec.speaker_hint_manager = DummyHintManager()

    errors: list[Exception] = []

    def _writer() -> None:
        try:
            for i in range(15):
                rec._commit_segment_event(
                    chunk_idx=i, time_str=f"00:00:{i:02d}", sec=i, text=f"Turn {i}",
                    wav_name=f"chunk_{i:05d}.wav", rms=0.05, spk="Alice",
                )
        except Exception as e:
            errors.append(e)

    def _hinter() -> None:
        try:
            for i in range(15):
                rec.apply_speaker_hint(quote=f"Turn {i}", speaker_name="Alice")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=_writer)
    t2 = threading.Thread(target=_hinter)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    written_lines = [ln for ln in rec.segments_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(written_lines) == 15
