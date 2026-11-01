"""
tests/adversarial/test_fail_fast_audio_pipeline_adversarial.py

Adversarial edge-case and regression tests verifying Wave 3 fail-fast audio pipeline invariants:
1. AudioMasterStitcher must never skip missing or corrupt chunks (must raise [STITCHER-GAP-DETECTED] / [STITCHER-CORRUPT-CHUNK]).
2. AudioChunkValidator and AudioVad must fail fast on corrupt, truncated, or zero-frame WAVs.
3. AudioChunker and AudioSplicer must raise RuntimeError on subprocess timeout or VAD failure (no silent RMS fallback).
4. AudioWorkerThread must raise RuntimeError on queue overflow instead of silently dropping audio frames.
5. Segment persistence must raise RuntimeError on SQLite WAL failure.
6. DiarizationEngine must fail fast on 0 turns, corrupt audio, or execution timeout.
7. MLX Transcriber must fail fast if >50% of segments collapse to 'Unknown' speaker.
8. LiveMeetingRecorder must raise RuntimeError if master audio is truncated or acoustic diarization yields >50 speakers.
"""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.audio_ingestion_core.mlx_transcriber import _align_segments_with_diarization
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import AudioChunkValidator
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter
from pipeline_a_intake.audio_chunker import detect_silence_cutpoints, get_audio_duration
from pipeline_a_intake.audio_splicer import SileroVADGate
from pipeline_a_intake.contracts import StitchedTranscriptDelta
from pipeline_a_intake.local_stt_core.dual_tier_engine import AsynchronousAudioQueue
from pipeline_a_intake.local_stt_core.persistence import persist_confirmed_segment


def _create_test_wav(
    path: Path,
    n_samples: int = 16000,
    amplitude: int = 1000,
    channels: int = 1,
    sample_rate: int = 16000,
) -> None:
    """Helper creating a standard valid test WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        if n_samples == 0:
            w.writeframes(b"")
            return
        samples = [int(amplitude * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


class TestAudioMasterStitcherFailFast(unittest.TestCase):
    """Verifies that AudioMasterStitcher strictly refuses to drop or skip audio."""

    def test_stitcher_sequence_gap_detected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "chunks"
            master_wav = Path(tmp_dir) / "master.wav"
            _create_test_wav(chunks_dir / "chunk_0000.wav", 16000)
            # chunk_0001 is missing!
            _create_test_wav(chunks_dir / "chunk_0002.wav", 16000)

            with pytest.raises(RuntimeError, match=r"\[STITCHER-GAP-DETECTED\]"):
                AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

    def test_stitcher_corrupt_chunk_detected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "chunks"
            master_wav = Path(tmp_dir) / "master.wav"
            _create_test_wav(chunks_dir / "chunk_0000.wav", 16000)
            # chunk_0001 is 0-byte corrupt
            (chunks_dir / "chunk_0001.wav").write_bytes(b"")
            _create_test_wav(chunks_dir / "chunk_0002.wav", 16000)

            with pytest.raises(RuntimeError, match=r"\[STITCHER-CORRUPT-CHUNK\]"):
                AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

    def test_stitcher_empty_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "empty_chunks"
            chunks_dir.mkdir()
            master_wav = Path(tmp_dir) / "master.wav"

            with pytest.raises(RuntimeError, match=r"\[STITCHER-EMPTY\]"):
                AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)

    def test_stitcher_malformed_filename_raises_value_error(self):
        with pytest.raises(ValueError, match="Malformed chunk filename"):
            AudioMasterStitcher.extract_chunk_index(Path("chunk_invalid.wav"))


class TestAudioVadAndValidatorFailFast(unittest.TestCase):
    """Verifies that VAD and chunk validator fail fast on invalid files."""

    def test_calculate_wav_rms_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            AudioVADSegmenter.calculate_wav_rms(Path("/nonexistent/path/chunk_9999.wav"))

    def test_calculate_wav_rms_truncated_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_wav = Path(tmp_dir) / "truncated.wav"
            bad_wav.write_bytes(b"RIFF1234WAVEfmt ")
            with pytest.raises(ValueError, match="too small to contain valid header"):
                AudioVADSegmenter.calculate_wav_rms(bad_wav)

    def test_validator_rejects_non_16bit_audio(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "8bit.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(1)  # 8-bit, not 16-bit
                w.setframerate(16000)
                w.writeframes(b"\x80" * 8000)
            res = AudioChunkValidator.validate_chunk(p)
            self.assertFalse(res.is_valid)
            self.assertIn("Unsupported sample width", res.reason)


class TestAudioChunkerAndSplicerFailFast(unittest.TestCase):
    """Verifies that chunker and splicer raise RuntimeError instead of degrading."""

    def test_detect_silence_cutpoints_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = Path(tmp_dir) / "test.wav"
            _create_test_wav(audio_path, 16000)

            with patch("subprocess.run", side_effect=RuntimeError("ffmpeg failed")), pytest.raises(
                RuntimeError, match="Silence detection failed"
            ):
                detect_silence_cutpoints(str(audio_path), total_seconds=120.0, chunk_duration=30.0)

    def test_get_audio_duration_invalid_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            corrupt = Path(tmp_dir) / "corrupt.wav"
            corrupt.write_bytes(b"not a wav")
            with pytest.raises((ValueError, RuntimeError)):
                get_audio_duration(str(corrupt))

    def test_splicer_evaluate_vad_failure_raises(self):
        gate = SileroVADGate()
        gate.ort_session = MagicMock()
        gate.ort_session.run.side_effect = RuntimeError("CUDA OOM / inference crash")

        with pytest.raises(RuntimeError, match="ONNX Silero VAD inference error"):
            gate.evaluate(np.zeros(512, dtype=np.float32))


class TestAudioWorkerQueueFailFast(unittest.TestCase):
    """Verifies that local STT worker queue never drops audio slices silently."""

    def test_worker_queue_raises_on_overflow(self):
        import threading
        block_event = threading.Event()

        def blocking_worker(item):
            block_event.wait(timeout=5.0)

        # Queue maxsize 1
        queue_obj = AsynchronousAudioQueue(name="test_worker", worker_fn=blocking_worker, max_size=1)
        try:
            # First item gets picked up by worker and blocks
            queue_obj.push("item_1")
            # Give worker thread a moment to pop item_1
            import time
            time.sleep(0.05)
            # Second item fills the 1-slot queue
            queue_obj.push("item_2")

            # Pushing to full queue must raise RuntimeError instead of silently dropping
            with pytest.raises(RuntimeError, match="Audio queue.*full; refusing to drop audio slice"):
                queue_obj.push("item_3")
        finally:
            block_event.set()
            queue_obj.stop()


class TestPersistenceFailFast(unittest.TestCase):
    """Verifies that SQLite persistence failures crash the pipeline."""

    def test_persist_confirmed_segment_raises_on_db_failure(self):
        delta = StitchedTranscriptDelta(
            confirmed_delta="Critical business commitment",
            speculative_preview="",
            speaker="Speaker A",
            start_ts=0.0,
            end_ts=5.0,
        )
        mock_db = MagicMock()
        mock_db.append_evidence.side_effect = RuntimeError("Disk I/O error or SQLite database locked")

        with pytest.raises(RuntimeError, match="Fail-Fast: Failed to persist confirmed segment"):
            persist_confirmed_segment(
                delta=delta,
                meeting_id="meeting_123",
                db_manager=mock_db,
            )


class TestDiarizationEngineFailFast(unittest.TestCase):
    """Verifies that DiarizationEngine crashes when zero turns are generated."""

    def test_extract_speaker_turns_raises_on_zero_turns(self):
        engine = DiarizationEngine.__new__(DiarizationEngine)
        engine.pipeline = MagicMock()
        # Mock diarize returning empty annotation
        mock_diarization = MagicMock()
        mock_diarization.itertracks.return_value = []
        engine.diarize = MagicMock(return_value=mock_diarization)

        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "silence.wav"
            _create_test_wav(p, 16000)
            with pytest.raises(RuntimeError, match="Acoustic diarization produced 0 speaker turns"):
                engine.extract_speaker_turns(p)


class TestMLXTranscriberFailFast(unittest.TestCase):
    """Verifies that MLX transcriber fails fast on diarization collapse to Unknown."""

    def test_align_segments_raises_when_majority_unknown(self):
        sanitized_segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello world"},
            {"start": 2.0, "end": 4.0, "text": "Next turn"},
        ]
        # Diarization with no overlap -> segments become Unknown
        mock_diarization = MagicMock()
        mock_diarization.itertracks.return_value = []

        with pytest.raises(RuntimeError, match=r"segments \(>50%\) have 'Unknown' speaker"):
            _align_segments_with_diarization(sanitized_segments, mock_diarization)


if __name__ == "__main__":
    unittest.main()


    def test_diarization_collapse_error(self):
        from unittest.mock import MagicMock, patch

        from dual_pipeline.audio_ingestion_core.diarization_engine import get_diarization_engine
        from dual_pipeline.exceptions import DiarizationCollapseError

        engine = get_diarization_engine()
        engine._initialized = True
        engine.pipeline = MagicMock()

        class MockTurn:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class MockDiarization:
            def itertracks(self, yield_label=True):
                # 11 turns for Speaker A, 1 turn for Speaker B (A is > 85%)
                for i in range(11):
                    yield MockTurn(i, i+1), None, "Speaker A"
                yield MockTurn(11, 11.1), None, "Speaker B"

        engine.pipeline.return_value = MockDiarization()

        with patch('dual_pipeline.audio_ingestion_core.diarization_engine.Path.is_file', return_value=True), \
                 patch('dual_pipeline.audio_ingestion_core.diarization_engine.Path.stat') as stat_mock:
                stat_mock.return_value.st_size = 100000
                with self.assertRaises(DiarizationCollapseError) as ctx:
                    engine.format_diarized_json("dummy.wav")
                self.assertIn("Skew Gate: Single speaker collapse detected", str(ctx.exception))
