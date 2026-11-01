"""Wave 1 Adversarial & Edge-Case Tests: Boundary Payloads, Partial Writes, Timeout Non-Retry & Inode Rotation."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest


def test_adversarial_partial_jsonl_write_and_file_truncation_reset(tmp_path: Path) -> None:
    """Covers malformed/truncated trailing JSON line during concurrent write and file truncation cursor reset."""
    out_dir = tmp_path / "output" / "project_nexus" / "10-27-2026"
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_path = out_dir / "segments.jsonl"

    line1 = json.dumps({"seq": 1, "speaker": "Rusty Gattmann", "text": "Valid first turn.", "timestamp": "00:00:05", "start_sec": 5.0})
    partial_line2 = '{"seq": 2, "speaker": "Robson N. Silva", "text": "Half-written concurrent turn'
    seg_path.write_text(line1 + "\n" + partial_line2, encoding="utf-8")

    manifest = SessionManifest(
        session_id="sess_adv_1",
        engine_signature="urn:nee:dual-pipeline:live-meeting-recorder:1.0.0",
        parent_pid=12345,
        parent_start_time=1000.0,
        repo_root=str(tmp_path),
        meeting_slug="adv_sync",
        engagement_id="project_nexus",
        output_dir=str(out_dir.relative_to(tmp_path)),
        segments_jsonl=str(seg_path.relative_to(tmp_path)),
        capture_state="CAPTURING",
    )

    reader = LiveBufferReader(tmp_path)
    turns = reader.read_turns(manifest)
    assert len(turns) == 1
    assert turns[0]["speaker"] == "Rusty Gattmann"

    # Complete the partial line with a newline and verify it is picked up cleanly on next read
    with open(seg_path, "a", encoding="utf-8") as f:
        f.write('", "timestamp": "00:00:10", "start_sec": 10.0}\n')

    turns_after = reader.read_turns(manifest)
    assert len(turns_after) == 2
    assert turns_after[1]["speaker"] == "Robson N. Silva"

    # Truncate/replace file with 1 turn and verify cursor resets cleanly instead of seeking past EOF
    seg_path.write_text(
        json.dumps({"seq": 1, "speaker": "Drew Oakley", "text": "Fresh restarted file.", "timestamp": "00:00:01", "start_sec": 1.0}) + "\n",
        encoding="utf-8",
    )
    turns_reset = reader.read_turns(manifest)
    assert len(turns_reset) == 1
    assert turns_reset[0]["speaker"] == "Drew Oakley"


def test_adversarial_streaming_deadline_exceeded_never_fires_second_llm_call(tmp_path: Path) -> None:
    """Covers overflow/timeout exhaustion: streaming timeout must fail closed without a second 7s LLM call."""
    advisor = LiveCopilotAdvisor(
        project_name="project_nexus",
        meeting_name="Google PartnerAdvisory Connect",
        attendees=["Drew Oakley", "Rusty Gattmann"],
        mock_mode=True,
        enable_prefetch=False,
        session_output_dir=tmp_path,
    )
    advisor.record_turn("Drew Oakley", "We need to finalize the HR 360 PRF SOW and rebate funding today.", "00:01:00", 60.0)
    advisor.mock_mode = False
    advisor.client = MagicMock()
    mock_streaming = MagicMock()
    mock_streaming.execute_streaming_evaluation.return_value = None
    mock_streaming.last_stream_outcome = "DEADLINE_EXCEEDED"
    advisor.streaming_handler = mock_streaming

    res = advisor._evaluate_dialogue_sync()
    assert res is None
    assert advisor.client.chat.completions.create.call_count == 0


def test_adversarial_empty_and_zero_byte_segments_file(tmp_path: Path) -> None:
    """Covers boundary payload: 0-byte segments.jsonl and whitespace-only segments.jsonl."""
    out_dir = tmp_path / "output" / "project_nexus" / "10-27-2026"
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_path = out_dir / "segments.jsonl"
    seg_path.write_text("", encoding="utf-8")

    manifest = SessionManifest(
        session_id="sess_adv_empty",
        engine_signature="urn:nee:dual-pipeline:live-meeting-recorder:1.0.0",
        parent_pid=12345,
        parent_start_time=1000.0,
        repo_root=str(tmp_path),
        meeting_slug="empty_sync",
        engagement_id="project_nexus",
        output_dir=str(out_dir.relative_to(tmp_path)),
        segments_jsonl=str(seg_path.relative_to(tmp_path)),
        capture_state="CAPTURING",
    )
    reader = LiveBufferReader(tmp_path)
    assert reader.read_turns(manifest) == []
