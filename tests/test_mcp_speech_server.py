"""Tests for Speech & Transcript Synthesizer MCP Server (Rank 2)."""
import json
import wave
from pathlib import Path

import pytest

from tools.mcp_speech_server import (
    audio_analyze_and_chunk,
    generate_synthesis_artifacts,
    transcript_stitch_speakers,
    validate_output_hierarchy_compliance,
)


@pytest.fixture
def temp_wav_file(tmp_path: Path):
    wav_path = tmp_path / "sample_meeting.wav"
    # Create a 2-second 16kHz mono WAV file
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        # Write 32000 silent frames (2 seconds)
        wf.writeframes(b"\x00" * 64000)
    return wav_path


def test_audio_analyze_and_chunk(temp_wav_file):
    res = audio_analyze_and_chunk(str(temp_wav_file), target_duration_sec=1)
    data = json.loads(res)
    assert data["sample_rate_hz"] == 16000
    assert data["channels"] == 1
    assert data["total_duration_sec"] == 2.0
    assert data["chunk_count"] == 2


def test_transcript_stitch_speakers():
    turns = [
        {"speaker": "Amit", "start_time": "00:00:01", "end_time": "00:00:05", "text": "Hello team."},
        {"speaker": "Amit", "start_time": "00:00:05", "end_time": "00:00:08", "text": "Let's review the architecture."},
        {"speaker": "Carlos", "start_time": "00:00:09", "end_time": "00:00:15", "text": "Sounds good."},
    ]
    res = transcript_stitch_speakers(json.dumps(turns))
    data = json.loads(res)
    assert data["original_turn_count"] == 3
    assert data["stitched_turn_count"] == 2
    assert "Hello team. Let's review the architecture." in data["stitched_turns"][0]["text"]


def test_generate_synthesis_artifacts_is_neutered_and_raises_runtime_error():
    turns = [
        {"speaker": "Amit", "start_time": "00:00:01", "end_time": "00:00:05", "text": "We agree on Phase 2."},
    ]
    with pytest.raises(RuntimeError, match="Direct artifact generation via shadow MCP tool 'generate_synthesis_artifacts' is prohibited"):
        generate_synthesis_artifacts("nexus_review", "09-10-2026", "Amit Ayer, Carlos Diaz", json.dumps(turns))


def test_validate_output_hierarchy_compliance():
    files = [
        "01_meet_09-10-2026_hitl_resolved_transcript.md",
        "02_meet_09-10-2026_executive_recap.md",
        "03_meet_09-10-2026_canonical_signal_projection.json",
    ]
    res = validate_output_hierarchy_compliance(
        "project_nexus",
        "09-10-2026",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/.worktrees/live_feature/output",
    )
    data = json.loads(res)
    assert data["status"] == "PASS"
    assert data["compliant"] is True

    # Test invalid date
    res_bad = validate_output_hierarchy_compliance(
        "project_nexus",
        "2026-09-10",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/.worktrees/live_feature/output",
    )
    data_bad = json.loads(res_bad)
    assert data_bad["status"] == "FAIL"

    # Test worktree isolation enforcement
    res_wt_pass = validate_output_hierarchy_compliance(
        "project_nexus",
        "09-10-2026",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/.worktrees/live_feature/output",
        enforce_worktree=True,
    )
    assert json.loads(res_wt_pass)["status"] == "PASS"

    res_wt_fail = validate_output_hierarchy_compliance(
        "project_nexus",
        "09-10-2026",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/output",
        enforce_worktree=True,
    )
    data_wt_fail = json.loads(res_wt_fail)
    assert data_wt_fail["status"] == "FAIL"
    assert any("Worktree isolation violation" in err for err in data_wt_fail["errors"])


def test_validate_output_hierarchy_compliance_enforces_worktree_by_default():
    """Verify that omitting enforce_worktree enforces worktree isolation by default."""
    files = [
        "01_meet_09-10-2026_hitl_resolved_transcript.md",
        "02_meet_09-10-2026_executive_recap.md",
        "03_meet_09-10-2026_canonical_signal_projection.json",
    ]
    # When non-worktree path is provided and enforce_worktree is omitted (default=True)
    res = validate_output_hierarchy_compliance(
        "project_nexus",
        "09-10-2026",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/output",
        # enforce_worktree omitted intentionally
    )
    data = json.loads(res)
    assert data["status"] == "FAIL"
    assert data["compliant"] is False
    assert any("Worktree isolation violation (Master Invariant 14)" in err for err in data["errors"])

    # When explicit opt-out is passed (enforce_worktree=False)
    res_opt_out = validate_output_hierarchy_compliance(
        "project_nexus",
        "09-10-2026",
        json.dumps(files),
        execution_path="/Users/user/Git/repo/output",
        enforce_worktree=False,
    )
    data_opt_out = json.loads(res_opt_out)
    assert data_opt_out["status"] == "PASS"
    assert data_opt_out["compliant"] is True

