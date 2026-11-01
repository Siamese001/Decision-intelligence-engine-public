"""Unit and integration tests for audio master stitcher and drain-before-shutdown ordering."""
from __future__ import annotations

import logging
import struct
import wave
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
    AudioMasterStitcher,
)


def _create_wav(path: Path, n_samples: int, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        data = struct.pack(f"<{n_samples}h", *[500] * n_samples)
        w.writeframes(data)


class TestAudioMasterStitcher:
    """Validates master WAV stitching, corrupt chunk skipping, and drain ordering."""

    def test_stitch_master_wav_success(self, tmp_path: Path) -> None:
        """Verifies master stitching succeeds for contiguous, valid audio chunks."""
        chunks_dir = tmp_path / "chunks"
        master_wav = tmp_path / "master.wav"

        _create_wav(chunks_dir / "chunk_0000.wav", 16000)
        _create_wav(chunks_dir / "chunk_0001.wav", 16000)

        success, stitched_count, total_sec = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

        assert success is True
        assert stitched_count == 2
        assert total_sec == pytest.approx(2.0, rel=1e-2)
        assert master_wav.exists()

        with wave.open(str(master_wav), "rb") as w:
            assert w.getnframes() == 32000
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1

    def test_stitch_master_wav_crashes_on_corrupt_chunk(self, tmp_path: Path) -> None:
        """Verifies fail-fast: master stitching crashes immediately on zero-byte or corrupt chunks."""
        chunks_dir = tmp_path / "chunks"
        master_wav = tmp_path / "master.wav"

        _create_wav(chunks_dir / "chunk_0000.wav", 16000)
        (chunks_dir / "chunk_0001.wav").write_bytes(b"")
        _create_wav(chunks_dir / "chunk_0002.wav", 16000)

        with pytest.raises(RuntimeError, match=r"\[STITCHER-CORRUPT-CHUNK\]"):
            AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

    def test_stitch_master_wav_crashes_on_sequence_gap(self, tmp_path: Path) -> None:
        """Verifies fail-fast: master stitching crashes immediately when chunk indices have gaps."""
        chunks_dir = tmp_path / "chunks"
        master_wav = tmp_path / "master.wav"

        _create_wav(chunks_dir / "chunk_0000.wav", 16000)
        # chunk_0001 is missing
        _create_wav(chunks_dir / "chunk_0002.wav", 16000)

        with pytest.raises(RuntimeError, match=r"\[STITCHER-GAP-DETECTED\]"):
            AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

    def test_validate_final_chunk_logs_truncation_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Verifies _validate_final_chunk detects truncated final chunk and logs warning."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Final Chunk Test",
            meeting_date="09/14/2026",
            output_dir=tmp_path / "out",
            segment_seconds=15,
            mock_mode=True,
        )

        # Expected size for 15s 16kHz mono = 15 * 16000 * 2 = 480,000 bytes
        # Create chunk_0000 full (480,000 bytes)
        _create_wav(recorder.chunks_dir / "chunk_0000.wav", 240000)
        # Create chunk_0001 truncated (< 50% = 50,000 samples = 100,000 bytes)
        _create_wav(recorder.chunks_dir / "chunk_0001.wav", 50000)

        with caplog.at_level(logging.WARNING):
            recorder._validate_final_chunk()

        assert any("[FINAL-CHUNK-TRUNCATION]" in record.message for record in caplog.records)

    def test_stop_drain_before_shutdown_call_order(self, tmp_path: Path) -> None:
        """Verifies stop() invokes _drain_remaining_chunks before _shutdown_background_services."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Order Test",
            meeting_date="09/14/2026",
            output_dir=tmp_path / "out",
            mock_mode=True,
        )
        # Populate one chunk so session does not rollback as empty
        _create_wav(recorder.chunks_dir / "chunk_0000.wav", 16000)
        recorder.processed_chunks.add("chunk_0000.wav")

        call_order: list[str] = []

        orig_drain = recorder._drain_remaining_chunks
        orig_shutdown = recorder._shutdown_background_services

        def mock_drain() -> None:
            call_order.append("drain")
            orig_drain()

        def mock_shutdown() -> None:
            call_order.append("shutdown")
            orig_shutdown()

        recorder._drain_remaining_chunks = mock_drain
        recorder._shutdown_background_services = mock_shutdown

        recorder.stop()

        assert "drain" in call_order
        assert "shutdown" in call_order
        # First call must be drain before shutdown
        assert call_order.index("drain") < call_order.index("shutdown")
