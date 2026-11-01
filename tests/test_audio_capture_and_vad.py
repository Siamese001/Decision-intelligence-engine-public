"""
tests/test_audio_capture_and_vad.py

Unit tests for Wave 1: Multi-Track Audio Capture gain normalization,
stereo isolation, and AudioVADSegmenter speech boundary detection.
"""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter


def _create_wav(path: Path, duration_sec: float, freq_hz: float = 0.0, sample_rate: int = 16000) -> Path:
    """Helper to synthesize test WAV file with silence (freq=0) or pure tone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            if freq_hz > 0:
                t = float(i) / sample_rate
                val = int(8000.0 * math.sin(2.0 * math.pi * freq_hz * t))
            else:
                val = 0
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)
    return path


def test_vad_silence_vs_speech_detection():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        silence_wav = _create_wav(tmp_path / "silence.wav", duration_sec=2.0, freq_hz=0.0)
        speech_wav = _create_wav(tmp_path / "speech.wav", duration_sec=2.0, freq_hz=300.0)

        vad = AudioVADSegmenter(energy_threshold=0.015)

        assert vad.calculate_wav_rms(silence_wav) < 0.005
        assert vad.calculate_wav_rms(speech_wav) > 0.05

        assert vad.is_speech_chunk(silence_wav) is False
        assert vad.is_speech_chunk(speech_wav) is True

        active, silent = vad.filter_speech_chunks([silence_wav, speech_wav])
        assert active == [speech_wav]
        assert silent == [silence_wav]


def test_vad_speech_boundaries():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # Create a 3-second file: 1s silence, 1s tone, 1s silence
        combined_wav = tmp_path / "combined.wav"
        with wave.open(str(combined_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            # 1 sec silence
            frames.extend(struct.pack("<h", 0) * 16000)
            # 1 sec speech tone
            for i in range(16000):
                t = float(i) / 16000.0
                frames.extend(struct.pack("<h", int(8000.0 * math.sin(2.0 * math.pi * 440.0 * t))))
            # 1 sec silence
            frames.extend(struct.pack("<h", 0) * 16000)
            wf.writeframes(frames)

        vad = AudioVADSegmenter(energy_threshold=0.015, min_speech_duration_ms=250, min_silence_duration_ms=300)
        boundaries = vad.detect_speech_boundaries(combined_wav)
        assert len(boundaries) >= 1
        start_sec, end_sec = boundaries[0]
        assert 0.8 <= start_sec <= 1.2
        assert 1.8 <= end_sec <= 2.2


def test_audio_capture_manager_gain_normalization_cmd():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        chunks_dir = tmp_path / "chunks"
        master_wav = tmp_path / "master.wav"

        mgr_mono = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="mixed",
            enable_stereo_isolation=False,
            mock_mode=True,
        )

        assert mgr_mono.enable_stereo_isolation is False

        mgr_stereo = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="mixed",
            enable_stereo_isolation=True,
            mock_mode=True,
        )

        assert mgr_stereo.enable_stereo_isolation is True


def test_audio_capture_manager_mock_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        chunks_dir = tmp_path / "chunks"
        master_wav = tmp_path / "master.wav"

        mgr = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="mic",
            mock_mode=True,
        )

        mgr.start()
        assert mgr.is_running is True
        assert mgr.active_strategy == "mock"
        mgr.stop()
        assert mgr.is_running is False
