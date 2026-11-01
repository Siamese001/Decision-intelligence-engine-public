"""Adversarial and edge-case tests for Phase 2: Copilot session discovery and readiness.
Covers:
1. Boundary payload (empty registry, 0-byte manifest, missing registry directory)
2. Malformed input (corrupted JSON manifest, truncated JSONL segments with invalid UTF-8)
3. Concurrency & race (concurrent background heartbeat updates while LiveBufferReader scans)
4. Invariant bypass attempt (conflicting session IDs in distinct registries resolving to different meetings fails closed)
5. Rollback & recovery (graceful degradation when candidate registries disappear or become unreadable)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from dual_pipeline.cli.copilot_session import _read_turns_from_jsonl
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    LiveBufferReader,
    _parse_jsonl_turns,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


def test_adversarial_boundary_empty_and_zero_byte_manifests(tmp_path: Path):
    """Category 1: Boundary payload - 0-byte files, temporary dotfiles, and empty manifests."""
    reg_dir = tmp_path / ".session_registry"
    reg_dir.mkdir(parents=True)

    # 0-byte file
    zero_file = reg_dir / "zero_manifest.json"
    zero_file.write_bytes(b"")

    # Temporary dotfile
    tmp_file = reg_dir / ".tmp_pending_write.json"
    tmp_file.write_text('{"session_id": "temp-123"}', encoding="utf-8")

    # Valid manifest
    valid_file = reg_dir / "valid_session.json"
    manifest_data = {
        "session_id": "valid-session-1",
        "engine_signature": "urn:nee:dual-pipeline:live-meeting-recorder:1.0.0",
        "parent_pid": os.getpid(),
        "parent_start_time": time.time(),
        "repo_root": str(tmp_path),
        "output_dir": "output/valid",
        "last_heartbeat": time.time(),
    }
    valid_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    reader = LiveBufferReader(tmp_path)
    active = reader.get_active_session()
    assert active is not None
    assert active.session_id == "valid-session-1"

    # Reading zero-byte manifest directly returns None without crash
    assert LiveBufferReader._read_manifest_file(zero_file) is None
    assert LiveBufferReader._read_manifest_file(tmp_file) is None


def test_adversarial_malformed_jsonl_and_invalid_utf8(tmp_path: Path):
    """Category 2: Malformed input - truncated JSON lines, unparseable rows, and invalid UTF-8 bytes."""
    output_dir = tmp_path / "meeting_output"
    cache_dir = output_dir / "cache"
    cache_dir.mkdir(parents=True)

    segments_file = cache_dir / "segments.jsonl"
    valid_turn = {"speaker": "Alice", "normalized": "Valid statement", "timestamp": "00:00:01", "start_sec": 1.0}
    
    # Write valid row, malformed row, partial row, and invalid UTF-8 surrogate
    with open(segments_file, "wb") as f:
        f.write(json.dumps(valid_turn).encode("utf-8") + b"\n")
        f.write(b'{"invalid_json_missing_brace: true\n')
        f.write(b'\xff\xfe\xfd\n')  # Invalid UTF-8 bytes
        f.write(json.dumps({"speaker": "Bob", "text": "Recovered second statement", "timestamp": "00:00:10"}).encode("utf-8") + b"\n")
        f.write(b'{"speaker": "Truncated", "text": "Unfinished')  # Truncated trailing row

    turns = _read_turns_from_jsonl(output_dir)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Alice"
    assert turns[0]["text"] == "Valid statement"
    assert turns[1]["speaker"] == "Bob"
    assert turns[1]["text"] == "Recovered second statement"


def test_adversarial_concurrent_registry_heartbeat_and_discovery(tmp_path: Path):
    """Category 3: Concurrency & race - reader scans candidate registries while writer executes rapid heartbeats."""
    reg = SessionRegistry(tmp_path)
    reg.register(
        session_id="concurrent-session-999",
        meeting_slug="concurrent_meeting",
        output_dir="output/concurrent_meeting",
    )

    reader = LiveBufferReader(tmp_path)
    stop_event = concurrent.futures.Future()

    def _writer_loop():
        for i in range(50):
            reg.heartbeat(
                "concurrent-session-999",
                force=True,
                capture_state="CAPTURING",
                asr_state="READY",
                context_state="FRESH" if i % 2 == 0 else "INITIALIZING",
            )
            time.sleep(0.002)

    def _reader_loop():
        observed = []
        for _ in range(50):
            manifest = reader.get_session("concurrent-session-999")
            if manifest:
                observed.append(manifest.session_id)
            time.sleep(0.002)
        return observed

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_write = executor.submit(_writer_loop)
        f_read = executor.submit(_reader_loop)
        f_write.result()
        observed_reads = f_read.result()

    assert len(observed_reads) > 0
    assert all(sid == "concurrent-session-999" for sid in observed_reads)


def test_adversarial_invariant_bypass_conflicting_session_ids_fail_closed(tmp_path: Path):
    """Category 4: Invariant bypass attempt - colliding session IDs across registries with distinct outputs must fail closed."""
    reg_dir1 = tmp_path / "repo1"
    reg_dir1.mkdir()
    (reg_dir1 / ".git").mkdir()
    reg1 = SessionRegistry(reg_dir1)

    reg_dir2 = tmp_path / "repo2"
    reg_dir2.mkdir()
    (reg_dir2 / ".git").mkdir()
    reg2 = SessionRegistry(reg_dir2)

    # Register colliding session ID pointing to two completely distinct meeting directories
    shared_id = "colliding-session-uuid"
    reg1.register(
        session_id=shared_id,
        meeting_slug="meeting_alpha",
        output_dir="output/meeting_alpha",
    )
    reg2.register(
        session_id=shared_id,
        meeting_slug="meeting_beta",
        output_dir="output/meeting_beta",
    )

    reader = LiveBufferReader(reg_dir1)
    # Manually configure candidate registries to include both conflicting repos
    reader._candidate_registries = lambda: [reg1, reg2]

    # Must fail closed and return None rather than silently returning the wrong meeting!
    manifest = reader.get_session(shared_id)
    assert manifest is None


def test_adversarial_rollback_and_missing_directory_recovery(tmp_path: Path):
    """Category 5: Rollback & recovery - candidate registry directory deleted or missing mid-lifecycle."""
    reg = SessionRegistry(tmp_path)
    reg.register(session_id="recovery-session-001", meeting_slug="recovery_meeting")

    reader = LiveBufferReader(tmp_path)
    assert reader.get_session("recovery-session-001") is not None

    # Delete the manifest file to simulate clean termination / cleanup
    (tmp_path / ".session_registry" / "recovery-session-001.json").unlink()

    # Querying deleted session returns None cleanly without throwing unhandled exceptions
    assert reader.get_session("recovery-session-001") is None
    assert reader.get_active_session() is None
