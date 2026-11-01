"""tests/test_live_recorder_mlx_streaming.py

Validates Wave 3 integration:
1. Local MLX Metal GPU Whisper chunk transcription with silence gating.
2. Anti-hallucination phrase filtering.
3. LiveMeetingRecorder integration with backend='mlx' and backend='auto'.
4. Parameter verification for mlx_whisper (language='en', without_timestamps=True, fp16=True).
5. CLI argument parsing for --backend flag in live_audio_transcriber.py.
"""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    HALLUCINATIONS,
    transcribe_meeting_chunk,
)


def _create_synthetic_wav(path: Path, duration_sec: float = 1.0, amplitude: int = 5000) -> None:
    sample_rate = 16000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # Create audio with tone/noise
        frames = bytearray()
        for i in range(n_frames):
            val = amplitude if (i % 32 < 16) else -amplitude
            frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


class TestLiveRecorderMLXStreaming(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_live_mlx_")
        self.temp_path = Path(self.temp_dir)
        self.test_wav = self.temp_path / "chunk_0000.wav"
        _create_synthetic_wav(self.test_wav, duration_sec=1.0, amplitude=4000)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_transcribe_meeting_chunk_mock_mode(self):
        result = transcribe_meeting_chunk(
            self.test_wav,
            backend="mlx",
            mock_mode=True,
        )
        self.assertIn("Mock transcript segment", result)

    def test_transcribe_meeting_chunk_silence_gating(self):
        silent_wav = self.temp_path / "silent.wav"
        _create_synthetic_wav(silent_wav, duration_sec=1.0, amplitude=10)

        # In LiveMeetingRecorder, silence gating checks rms < 80
        recorder = LiveMeetingRecorder(
            output_dir=self.temp_path,
            mock_mode=False,
            backend="mlx",
        )
        result = recorder._transcribe_chunk(silent_wav)
        self.assertEqual(result, "")

    @mock.patch("mlx_whisper.transcribe")
    @mock.patch("pipeline_a_intake.local_stt_core.resolve_model_path")
    def test_transcribe_meeting_chunk_mlx_backend_params(self, mock_resolve, mock_transcribe):
        mock_resolve.return_value = "/mock/model/path"
        mock_transcribe.return_value = {"text": "This is a real-time speech test."}

        result = transcribe_meeting_chunk(
            self.test_wav,
            backend="mlx",
            mock_mode=False,
        )
        self.assertEqual(result, "This is a real-time speech test.")
        mock_transcribe.assert_called_once()
        kwargs = mock_transcribe.call_args[1]
        self.assertEqual(kwargs.get("language"), "en")
        self.assertEqual(kwargs.get("without_timestamps"), False)
        self.assertEqual(kwargs.get("fp16"), True)
        self.assertEqual(kwargs.get("temperature"), (0.0, 0.2, 0.4))
        self.assertEqual(kwargs.get("condition_on_previous_text"), False)

    @mock.patch("mlx_whisper.transcribe")
    def test_anti_hallucination_filter(self, mock_transcribe):
        for hallucination in HALLUCINATIONS:
            mock_transcribe.return_value = {"text": f"Hey everyone, {hallucination} today."}
            result = transcribe_meeting_chunk(
                self.test_wav,
                backend="mlx",
                mock_mode=False,
            )
            self.assertEqual(result, "", f"Failed to filter hallucination: {hallucination}")

    @mock.patch(
        "dual_pipeline.live_meeting_recorder_core.chunk_transcriber._transcribe_mlx",
        return_value="Nexus pipeline architecture aligned.",
    )
    def test_live_recorder_end_to_end_mlx_chunk_processing(self, mock_transcribe_mlx):
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Wave3 MLX Test",
            meeting_date="09/14/2026",
            attendees="Amit Ayer",
            output_dir=self.temp_path,
            backend="mlx",
            mock_mode=False,
            enable_ws=False,
            enable_copilot=False,
            isolated_process=False,  # Disable IsolatedMLXTranscriberClient subprocess
        )

        recorder._init_transcript_file()
        chunk_file = recorder.chunks_dir / "chunk_0000.wav"
        _create_synthetic_wav(chunk_file, duration_sec=1.0, amplitude=4000)

        text = recorder.process_chunk(chunk_file, chunk_idx=0)
        recorder.processed_chunks.add(chunk_file.name)

        self.assertEqual(text, "Nexus pipeline architecture aligned.")
        self.assertTrue(mock_transcribe_mlx.called, "Expected _transcribe_mlx to be called")

        recorder.stop()
        self.assertIn("chunk_0000.wav", recorder.processed_chunks)

        # Verify segments.jsonl
        self.assertTrue(recorder.segments_jsonl.exists())
        with open(recorder.segments_jsonl, "r", encoding="utf-8") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            seg = json.loads(lines[0])
            self.assertEqual(seg["text"], "Nexus pipeline architecture aligned.")
            self.assertEqual(seg["chunk_idx"], 0)

        # Verify live transcript markdown
        content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Nexus pipeline architecture aligned.", content)
        self.assertIn("Amit Ayer", content)

    def test_recorder_initializes_with_backend_param(self):
        rec_auto = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, backend="auto")
        self.assertEqual(rec_auto.backend, "auto")

        rec_mlx = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, backend="mlx")
        self.assertEqual(rec_mlx.backend, "mlx")

        rec_openai = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, backend="openai")
        self.assertEqual(rec_openai.backend, "openai")

    def test_recorder_initializes_with_audio_source_param(self):
        rec_auto = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, audio_source="auto")
        self.assertEqual(rec_auto.audio_source, "auto")

        rec_sys = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, audio_source="system")
        self.assertEqual(rec_sys.audio_source, "system")

        rec_mixed = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, audio_source="mixed")
        self.assertEqual(rec_mixed.audio_source, "mixed")

        rec_mic = LiveMeetingRecorder(output_dir=self.temp_path, mock_mode=True, audio_source="mic")
        self.assertEqual(rec_mic.audio_source, "mic")

    def test_capture_manager_strategy_resolution_headphones(self):
        from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager

        mgr = LiveAudioCaptureManager(
            chunks_dir=self.temp_path / ".chunks",
            master_wav=self.temp_path / "master.wav",
            source_mode="auto",
            mock_mode=False,
        )

        with mock.patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=True), \
             mock.patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
             mock.patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=True):
            strat, _ = mgr.resolve_source_strategy()
            # When headphones connected, must use digital system tap or mixed to hear remote participants
            self.assertIn(strat, ("mixed_sck", "screen_capture_kit", "loopback"))

    def test_capture_manager_strategy_resolution_muted(self):
        from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager

        mgr = LiveAudioCaptureManager(
            chunks_dir=self.temp_path / ".chunks",
            master_wav=self.temp_path / "master.wav",
            source_mode="auto",
            mock_mode=False,
        )

        with mock.patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=False), \
             mock.patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=True), \
             mock.patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=True):
            strat, _ = mgr.resolve_source_strategy()
            # When system is muted, mic hears nothing; must use digital system tap or mixed
            self.assertIn(strat, ("mixed_sck", "screen_capture_kit", "loopback"))

    def test_capture_manager_explicit_modes(self):
        from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager

        # System mode
        mgr_sys = LiveAudioCaptureManager(
            chunks_dir=self.temp_path / ".chunks",
            master_wav=self.temp_path / "master.wav",
            source_mode="system",
            mock_mode=False,
        )
        with mock.patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=True):
            strat, _ = mgr_sys.resolve_source_strategy()
            self.assertIn(strat, ("screen_capture_kit", "loopback"))

        # Mic mode
        mgr_mic = LiveAudioCaptureManager(
            chunks_dir=self.temp_path / ".chunks",
            master_wav=self.temp_path / "master.wav",
            source_mode="mic",
            mock_mode=False,
        )
        strat, _ = mgr_mic.resolve_source_strategy()
        self.assertEqual(strat, "microphone")

        # Mixed mode
        mgr_mixed = LiveAudioCaptureManager(
            chunks_dir=self.temp_path / ".chunks",
            master_wav=self.temp_path / "master.wav",
            source_mode="mixed",
            mock_mode=False,
        )
        with mock.patch("dual_pipeline.live_meeting_recorder_core.audio_capture.is_sck_tap_available", return_value=True):
            strat, _ = mgr_mixed.resolve_source_strategy()
            self.assertIn(strat, ("mixed_sck", "mixed_loopback"))

    @mock.patch("mlx_whisper.transcribe")
    @mock.patch("pipeline_a_intake.local_stt_core.resolve_model_path")
    def test_transcribe_meeting_chunk_forwards_initial_prompt(self, mock_resolve, mock_transcribe):
        mock_resolve.return_value = "/mock/model/path"
        mock_transcribe.return_value = {"text": "Prompt-steered transcription output."}

        custom_prompt = "Specific engineering terminology: GraphQL, BGE-M3, ScreenCaptureKit"
        result = transcribe_meeting_chunk(
            self.test_wav,
            backend="mlx",
            whisper_prompt=custom_prompt,
            mock_mode=False,
        )
        self.assertEqual(result, "Prompt-steered transcription output.")
        mock_transcribe.assert_called_once()
        kwargs = mock_transcribe.call_args[1]
        self.assertEqual(kwargs.get("initial_prompt"), custom_prompt)

    def test_live_audio_transcriber_cli_arguments(self):
        import subprocess
        import sys
        py_bin = sys.executable
        # Canonical CLI entrypoint
        res = subprocess.run(
            [py_bin, "nee.py", "record-meeting", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--audio-source", res.stdout)
        self.assertIn("--backend", res.stdout)
        self.assertIn("--whisper-model", res.stdout)

        # Decommissioned script per Master Invariant 10
        res_dep = subprocess.run(
            [py_bin, "scripts/live_audio_transcriber.py", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_dep.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res_dep.stderr)


if __name__ == "__main__":
    unittest.main()
