from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_recorder_anonymous_state_machine():
    recorder = LiveMeetingRecorder(
        project_name=None,
        meeting_name=None,
        attendees=None,
        dry_run=True,
        immediate_worktree=True, # Even if true, it shouldn't materialize if anonymous
        enable_hitl=True
    )

    assert recorder.is_anonymous is True
    assert recorder.clean_slug.startswith("anon_session_")

    # Check that worktree wasn't created
    assert getattr(recorder, "_worktree_materialized", False) is False

    # Ensure memory buffering structures exist
    assert hasattr(recorder, "_anonymous_transcript_buffer")
    assert hasattr(recorder, "_anonymous_segments_buffer")

    # It shouldn't crash initializing
    assert recorder.session_id is not None
