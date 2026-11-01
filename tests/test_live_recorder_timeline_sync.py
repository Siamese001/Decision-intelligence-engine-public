"""Integration tests for live meeting recorder timeline synchronization,
sample-accurate chunk offsets, and acoustic alignment overwrite guards.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder import (
    AlignmentDesynchronizationError,
    LiveMeetingRecorder,
    _check_cluster_collapse,
    _verify_temporal_sync,
)


class LiveRecorderTimelineSyncTests(unittest.TestCase):
    """Test suite for timeline drift prevention and alignment guards."""

    def test_chunk_timeline_skew_across_300_chunks(self) -> None:
        """Verify clock skew across 300 sequential 10s chunks is < 0.05s."""
        recorder = MagicMock(spec=LiveMeetingRecorder)
        recorder.segment_seconds = 10
        recorder._chunk_timings = {}
        recorder._cumulative_elapsed_seconds = 0.0
        recorder._get_chunk_duration = MagicMock(return_value=10.0)

        for i in range(300):
            start_sec, dur, end_sec = LiveMeetingRecorder._compute_chunk_timeline(
                recorder, i, Path(f"/fake/chunk_{i:05d}.wav")
            )
            expected_start = i * 10.0
            skew = abs(start_sec - expected_start)
            self.assertLess(skew, 0.05, f"Skew at chunk {i} was {skew:.4f}s >= 0.05s")
            self.assertEqual(dur, 10.0)
            self.assertEqual(end_sec, expected_start + 10.0)

    def test_chunk_156_drift_elimination_on_dropped_chunks(self) -> None:
        """When chunks 50-100 are missing, chunk 156 must reflect the true time offset, not drift backwards."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chunks_dir = Path(tmpdir)
            recorder = MagicMock(spec=LiveMeetingRecorder)
            recorder.segment_seconds = 10
            recorder.chunks_dir = chunks_dir
            recorder._chunk_timings = {}
            recorder._cumulative_elapsed_seconds = 0.0
            recorder._get_chunk_duration = MagicMock(return_value=10.0)

            # Process chunks 0..49
            for i in range(50):
                LiveMeetingRecorder._compute_chunk_timeline(
                    recorder, i, Path(f"/fake/chunk_{i:05d}.wav")
                )

            # Jump directly to chunk 156 (simulating chunks 50..155 not transcribed/cached)
            start_sec, dur, end_sec = LiveMeetingRecorder._compute_chunk_timeline(
                recorder, 156, Path(f"/fake/chunk_{156:05d}.wav")
            )
            # True nominal start for chunk 156 is 1560s, NOT 500s or 555s!
            self.assertGreaterEqual(start_sec, 1500.0)
            self.assertLessEqual(start_sec, 1600.0)

    def test_verify_temporal_sync_catches_divergence(self) -> None:
        """When segments diverge from acoustic turns by > 30s, raise AlignmentDesynchronizationError."""
        turns = [{"start": 1500.0, "end": 1530.0, "speaker": "SPEAKER_00"}]
        # Desynchronized segments at 500s
        segments = [{"seconds": 500.0, "duration": 10.0, "text": "Hello world"}]

        with self.assertRaises(AlignmentDesynchronizationError) as ctx:
            _verify_temporal_sync(turns, segments, threshold_sec=30.0)
        self.assertIn("Catastrophic timeline desynchronization", str(ctx.exception))

    def test_verify_temporal_sync_passes_when_aligned(self) -> None:
        """When segments overlap acoustic turns within 30s, verification passes cleanly."""
        turns = [{"start": 10.0, "end": 25.0, "speaker": "SPEAKER_00"}]
        segments = [{"seconds": 12.0, "duration": 10.0, "text": "Valid speech segment"}]
        # Should not raise
        _verify_temporal_sync(turns, segments, threshold_sec=30.0)

    def test_check_cluster_collapse_detects_overwrites(self) -> None:
        """When 5 acoustic clusters collapse to 1 aligned speaker, return True to reject overwrite."""
        turns = [
            {"start": 0.0, "end": 10.0, "speaker": f"SPEAKER_0{i}"} for i in range(5)
        ]
        # Collapsed segments: all attributed to single speaker
        segments = [
            {"seconds": i * 10.0, "duration": 10.0, "text": f"speech {i}", "speaker": "Amit Ayer", "speaker_resolved": "Amit Ayer"}
            for i in range(5)
        ]
        self.assertTrue(_check_cluster_collapse(turns, segments))

    def test_check_cluster_collapse_allows_multi_speaker_alignment(self) -> None:
        """When 5 acoustic clusters map to >= 4 speakers, return False."""
        turns = [
            {"start": 0.0, "end": 10.0, "speaker": f"SPEAKER_0{i}"} for i in range(5)
        ]
        speakers = ["Amit Ayer", "Drew Oakley", "Alex Brannan", "Dianne Eldridge", "Vani Eswarappa"]
        segments = [
            {"seconds": i * 10.0, "duration": 10.0, "text": f"speech {i}", "speaker": speakers[i], "speaker_resolved": speakers[i]}
            for i in range(5)
        ]
        self.assertFalse(_check_cluster_collapse(turns, segments))


if __name__ == "__main__":
    unittest.main()
