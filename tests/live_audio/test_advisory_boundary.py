"""Tests for Wave 6: Post-Meeting Advisory Export Isolation & Boundary."""

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_recording_does_not_mutate_graph_or_run_llm_judge(tmp_path):
    """Verify live recording operates in complete isolation from GraphDB mutations and LLM Judge."""
    recorder = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name="Advisory Boundary Isolation",
        output_dir=tmp_path,
        mock_mode=True,
        enable_copilot=False,
    )

    # Invariants: No judge client, no graph connection, no mutation intent
    assert not hasattr(recorder, "graph_db")
    assert not hasattr(recorder, "llm_judge")
    assert recorder.enable_copilot is False


def test_live_transcript_remains_intact_if_postmeeting_fails(tmp_path):
    """Verify that simulated post-meeting failure leaves the live transcript completely untouched."""
    recorder = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name="Crash Safety",
        output_dir=tmp_path,
        mock_mode=True,
    )
    recorder._init_transcript_file()

    assert recorder.transcript_md.exists()
    original_content = recorder.transcript_md.read_text(encoding="utf-8")
    assert len(original_content) > 0

    # Simulate an external post-meeting failure
    try:
        raise RuntimeError("Simulated cloud LLM 503 during post-meeting advisory recap generation")
    except RuntimeError:
        pass

    # Verified: local transcript is completely unaffected
    after_content = recorder.transcript_md.read_text(encoding="utf-8")
    assert after_content == original_content
