from unittest.mock import patch

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


@pytest.fixture
def temp_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root

@patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg")
@patch("dual_pipeline.live_meeting_recorder.os.getcwd")
@patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree")
def test_atomic_spool_migration(mock_ensure_wt, mock_getcwd, mock_ffmpeg, temp_repo):
    mock_getcwd.return_value = str(temp_repo)

    # Mock ensure_live_transcription_worktree to return a fake worktree dir
    wt_dir = temp_repo / ".worktrees" / "live_test_123"
    wt_dir.mkdir(parents=True)
    mock_ensure_wt.return_value = (wt_dir, "feat/live-test-123")

    recorder = LiveMeetingRecorder(
        project_name=None,
        meeting_name=None,
        attendees=None,
        dry_run=True,
        immediate_worktree=True,
        mock_mode=True,
        enable_hitl=True
    )

    assert recorder.is_anonymous is True
    assert recorder.worktree_dir is None

    # Simulate writing chunks and buffers in anonymous mode
    recorder.chunks_dir.mkdir(parents=True, exist_ok=True)
    (recorder.chunks_dir / "chunk_001.wav").touch()
    (recorder.chunks_dir / "chunk_002.wav").touch()

    recorder._anonymous_transcript_buffer = ["Hello world"]
    recorder._anonymous_segments_buffer = [{"text": "Hello world"}]

    old_chunks_dir = recorder.chunks_dir

    recorder.resolve_identity(
        project_name="project_nexus",
        meeting_name="design review",
        attendees=["Alice", "Bob"]
    )

    assert recorder.is_anonymous is False
    assert recorder.worktree_dir == wt_dir

    # Check chunks were moved
    assert len(list(recorder.chunks_dir.glob("chunk_*.wav"))) == 2
    assert recorder.chunks_dir != old_chunks_dir

    # Check buffers flushed
    assert len(recorder._anonymous_transcript_buffer) == 0
    assert len(recorder._anonymous_segments_buffer) == 0
    assert recorder.transcript_md.exists()
    assert recorder.segments_jsonl.exists()

    with open(recorder.transcript_md, "r") as f:
        assert "Hello world" in f.read()
