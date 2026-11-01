"""Tests for Wave 4: Durable Audio Capture, Monotonic Chunking, and Crash Recovery."""

import json

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_session_manifest_atomic_lifecycle(tmp_path):
    """Verify session manifest is created via atomic .tmp -> replace lifecycle."""
    recorder = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name="Atomic Manifest Test",
        output_dir=tmp_path,
        mock_mode=True,
    )

    assert recorder.session_manifest.exists()
    assert not recorder.session_manifest.with_suffix(".tmp").exists()

    data = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
    assert data["project"] == "project_nexus"
    assert data["meeting"] == "Atomic Manifest Test"
    assert "started_at" in data
    assert "master_wav" in data


def test_monotonic_chunk_resumption(tmp_path):
    """Verify recorder resumes chunk numbering monotonically if previous chunks exist."""
    chunks_dir = tmp_path / ".chunks_Resume_Test"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # Simulate existing chunks 0, 1, 2
    (chunks_dir / "chunk_000.wav").touch()
    (chunks_dir / "chunk_001.wav").touch()
    (chunks_dir / "chunk_002.wav").touch()

    recorder = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name="Resume Test",
        output_dir=tmp_path,
        mock_mode=True,
    )

    # Start index must be 3
    assert recorder.start_chunk_idx == 3


def test_processed_chunks_recovery_from_jsonl(tmp_path):
    """Verify recorder populates processed_chunks from existing segments.jsonl on recovery."""
    clean_slug = "Processed_Recovery_Test"
    date_str = "09-16-2026"
    segments_jsonl = tmp_path / f"01_{date_str}_{clean_slug}_segments.jsonl"

    entries = [
        {"chunk_file": "chunk_000.wav", "text": "First segment spoken."},
        {"chunk_file": "chunk_001.wav", "text": "Second segment spoken."},
    ]
    with open(segments_jsonl, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    recorder = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name=clean_slug,
        meeting_date="09/16/2026",
        output_dir=tmp_path,
        mock_mode=True,
    )

    assert "chunk_000.wav" in recorder.processed_chunks
    assert "chunk_001.wav" in recorder.processed_chunks
    assert len(recorder.processed_chunks) == 2
