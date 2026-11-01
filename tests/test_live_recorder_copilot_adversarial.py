"""Adversarial and edge-case tests for Wave 5 Live Recorder & Copilot (CAT-3)."""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.copilot_engine import _find_matching_assertion
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


# Category 1: Invariant bypass attempt (ungrounded line in _find_matching_assertion)
def test_adversarial_find_matching_assertion_rejects_zero_overlap_and_ungrounded_confidence() -> None:
    """CAT3-P1-2: Lines with zero lexical overlap return None instead of assertions[0]; conflict lines use min real confidence."""
    assertions = [
        {"assertion_id": "a1", "claim_text": "Database migration scheduled for Friday", "confidence_score": 0.45},
        {"assertion_id": "a2", "claim_text": "Database migration delayed to Monday", "confidence_score": 0.55},
    ]
    assert _find_matching_assertion("Could we clarify the point?", assertions) is None
    assert _find_matching_assertion("Unrelated quantum physics discussion", assertions) is None

    conflict_line = "Database migration was described as scheduled and delayed; which status is current?"
    matched = _find_matching_assertion(conflict_line, assertions)
    assert matched is not None
    assert matched["confidence_score"] == 0.45


# Category 2: Rollback & recovery (resolve_identity migrates existing on-disk segments.jsonl and transcript_md)
def test_adversarial_resolve_identity_migrates_on_disk_anonymous_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAT3-P0-3: resolve_identity migrates already-flushed anonymous segments.jsonl and transcript_md to worktree."""
    rec = object.__new__(LiveMeetingRecorder)
    rec.is_anonymous = True
    rec.project_name = "unknown"
    rec.meeting_name = "Anonymous"
    rec.attendees = []
    rec.date_dirname = "2026-10-15"
    rec.clean_slug = "anon_slug"
    rec.session_tag = "120000"

    anon_dir = tmp_path / "anon_staging"
    anon_dir.mkdir()
    rec.chunks_dir = anon_dir / "chunks"
    rec.chunks_dir.mkdir()
    rec.segments_jsonl = anon_dir / "01_segments.jsonl"
    rec.transcript_md = anon_dir / "01_transcript.md"
    rec.copilot_jsonl = anon_dir / "01_copilot.jsonl"
    rec.asr_filter_log = anon_dir / "asr_filter.jsonl"
    rec.master_wav = anon_dir / "master.wav"
    rec.session_manifest = anon_dir / "manifest.json"
    rec.acoustic_evidence_receipt_path = anon_dir / "receipt.json"
    rec.referenced_artifacts_dir = anon_dir / "ref"
    rec.ref_dir = rec.referenced_artifacts_dir
    rec.collateral_drop_log_path = anon_dir / "collateral.json"
    rec._anonymous_segments_buffer = []
    rec._anonymous_transcript_buffer = []
    rec.chat_attributions = None
    rec.speaker_hint_manager = None
    rec.voice_registry = None

    rec.segments_jsonl.write_text('{"seg": 1}\n', encoding="utf-8")
    rec.transcript_md.write_text("# Pre-resolution turn\n", encoding="utf-8")

    wt_dir = tmp_path / "wt_resolved"
    wt_dir.mkdir()
    monkeypatch.setattr(
        "dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree",
        lambda **kw: (wt_dir, "transcript/test-branch"),
    )

    rec.resolve_identity("nea", "Grid Sync", ["Alice Smith"])
    assert rec.segments_jsonl.is_file()
    assert '{"seg": 1}' in rec.segments_jsonl.read_text(encoding="utf-8")
    assert "# Pre-resolution turn" in rec.transcript_md.read_text(encoding="utf-8")


# Category 3: Concurrency & race (retryable ASR worker failure discards from _Queued_chunks safely)
def test_adversarial_asr_worker_failure_allows_retry_on_first_failure(tmp_path: Path) -> None:
    """CAT3-P0-1: When process_chunk fails once, _record_chunk_failure clears _Queued_chunks so chunk can be retried."""
    rec = object.__new__(LiveMeetingRecorder)
    rec.processed_chunks = set()
    rec.transcribed_chunks = set()
    rec._Queued_chunks = {"chunk_0002.wav"}
    rec._chunks_lock = threading.Lock()
    rec._chunk_failures = {}
    rec._consecutive_asr_failures = 0
    rec._asr_failed = False
    rec._chunk_queue = queue.Queue()

    cf = tmp_path / "chunk_0002.wav"
    cf.write_bytes(b"RIFF")

    def _fail_once(c, idx):
        raise RuntimeError("Transient Metal OOM")

    rec.process_chunk = _fail_once  # type: ignore[method-assign]
    rec._chunk_queue.put((cf, 2))
    rec._chunk_queue.put(None)
    rec._asr_worker_loop()

    assert "chunk_0002.wav" not in rec.processed_chunks
    assert "chunk_0002.wav" not in rec._Queued_chunks
    assert rec._chunk_failures["chunk_0002.wav"] == 1
