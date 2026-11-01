"""
tests/test_live_meeting_recorder_copilot_e2e.py

End-to-End Integration test: Ingestion -> Inflection Detection -> Copilot Advisory -> Broadcast.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_live_meeting_recorder_inflection_copilot_e2e(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        recorder = LiveMeetingRecorder(
            project_name="enterprise_account_planning",
            meeting_name="Test_Inflection_Meeting",
            output_dir=output_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=True,
            isolated_process=False,
        )

        assert recorder.inflection_detector is not None
        assert recorder.copilot_advisor is not None

        # Create dummy wav file
        dummy_wav = recorder.chunks_dir / "chunk_0001.wav"
        dummy_wav.write_bytes(b"RIFFdummywavdata")

        # Mock transcription to return an interrogative hand-off
        question_text = "Who owns the staffing machinery, and who defines the technical bar?"
        monkeypatch.setattr(recorder, "_transcribe_chunk", lambda wav: question_text)

        # Process chunk
        res_text = recorder.process_chunk(dummy_wav, chunk_idx=1)
        assert res_text == question_text

        # Wait briefly for async copilot worker to complete
        time.sleep(0.3)

        # Verify feedback written to copilot_jsonl
        assert recorder.copilot_jsonl.exists()
        lines = [json.loads(l) for l in recorder.copilot_jsonl.read_text(encoding="utf-8").strip().splitlines()]
        assert len(lines) >= 1
        latest = lines[-1]
        assert latest["type"] == "COPILOT_FEEDBACK"
        assert latest["inflection_trigger"] == "STOPPING_POINT_QUESTION"
        assert latest["is_priority"] is True
        assert "suggested_script" in latest

        # Now test an acoustic pause chunk
        pause_wav = recorder.chunks_dir / "chunk_0002.wav"
        pause_wav.write_bytes(b"RIFFdummypausewav")
        monkeypatch.setattr(recorder, "_transcribe_chunk", lambda wav: "")
        monkeypatch.setattr("dual_pipeline.live_meeting_recorder.get_audio_rms", lambda wav: 0.002)

        # Feeding silence after active speech triggers pause
        recorder.process_chunk(pause_wav, chunk_idx=2)
        time.sleep(0.3)

        lines_after_pause = [json.loads(l) for l in recorder.copilot_jsonl.read_text(encoding="utf-8").strip().splitlines()]
        assert len(lines_after_pause) >= 2
        pause_feedback = lines_after_pause[-1]
        assert pause_feedback["inflection_trigger"] == "STOPPING_POINT_PAUSE"
        assert pause_feedback["is_priority"] is True

        recorder.stop()
