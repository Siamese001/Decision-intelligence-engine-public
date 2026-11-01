"""
Unit tests for live meeting transcript title and date enforcement in header initialization and disaster recovery.
"""
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.live_meeting_recorder_core.audio_utils import init_live_transcript_file
from dual_pipeline.live_meeting_recorder_core.disaster_recovery import SessionRecoveryManager
from dual_pipeline.live_meeting_recorder_core.recording_service_models import RecordingStartRequest


def test_init_live_transcript_file_incorporates_meeting_name_and_date():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        transcript_md = tmp_path / "01_test_meeting_live_transcript.md"
        output_dir = tmp_path / "output"
        cache_dir = tmp_path / "cache"

        init_live_transcript_file(
            transcript_md=transcript_md,
            output_dir=output_dir,
            cache_dir=cache_dir,
            meeting_name="Architecture Council Review",
            meeting_date_display="10/12/2026",
            project_name="project_nexus",
            attendees=[{"name": "Amit Ayer", "role": "Principal AI Engineer", "org": "PartnerAdvisory"}],
        )

        content = transcript_md.read_text(encoding="utf-8")
        lines = content.splitlines()

        assert len(lines) > 0
        assert lines[0] == "# Live Meeting Transcript: Architecture Council Review | 10/12/2026"
        assert "**Date:** 10/12/2026" in content
        assert "**Project:** `project_nexus`" in content


def test_init_live_transcript_file_defaults_when_meeting_name_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        transcript_md = tmp_path / "01_anon_live_transcript.md"

        init_live_transcript_file(
            transcript_md=transcript_md,
            output_dir=tmp_path / "output",
            cache_dir=tmp_path / "cache",
            meeting_name="",
            meeting_date_display="10/12/2026",
            project_name="adhoc",
            attendees=[],
        )

        first_line = transcript_md.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "# Live Meeting Transcript: Live Meeting | 10/12/2026"


def test_disaster_recovery_sealer_incorporates_meeting_name_and_date():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        target_path = tmp_path / "recovered_transcript.md"

        SessionRecoveryManager.reconstruct_transcript(
            segments=[{"seconds": 0.0, "speaker": "Amit", "text": "Testing recovery"}],
            meeting="Operations Review",
            date="10/12/2026",
            project="project_nexus",
            valid_chunks_count=1,
            duration_sec=15.0,
            recovery_rate=1.0,
            master_wav_name="master.wav",
            target_transcript_path=target_path,
        )

        content = target_path.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        assert first_line == "# Live Meeting Transcript: Operations Review | 10/12/2026"


def test_recording_start_request_carries_meeting_date():
    req = RecordingStartRequest(
        meeting_name="Standup",
        meeting_date="10/12/2026",
    )
    assert req.meeting_date == "10/12/2026"
    assert req.to_dict()["meeting_date"] == "10/12/2026"
