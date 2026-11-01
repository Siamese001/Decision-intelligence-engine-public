"""Unit tests for pipeline_a_intake.audio_chunker.

Verifies speaker normalization, silence cutpoint fallbacks, and multi-chunk segment stitching.
"""
from __future__ import annotations

import unittest

from pipeline_a_intake.audio_chunker import (
    clean_speaker_label,
    detect_silence_cutpoints,
    stitch_and_align_segments,
)


class TestAudioChunkerUnit(unittest.TestCase):
    def test_clean_speaker_label_standardizes_labels(self) -> None:
        self.assertEqual(clean_speaker_label("Speaker 1"), "Speaker 1")
        self.assertEqual(clean_speaker_label("speaker_2"), "speaker_2")
        self.assertEqual(clean_speaker_label("Speaker Alice"), "Speaker Alice")
        self.assertEqual(clean_speaker_label("1"), "Speaker 1")
        self.assertEqual(clean_speaker_label("Bob"), "Speaker Bob")
        self.assertEqual(clean_speaker_label(42), "Speaker 42")

    def test_detect_silence_cutpoints_short_audio(self) -> None:
        # If total_seconds <= chunk_duration (default 300.0)
        cutpoints = detect_silence_cutpoints("nonexistent.wav", 120.0, chunk_duration=300.0)
        self.assertEqual(cutpoints, [0.0, 120.0])

        # Zero or negative seconds
        self.assertEqual(detect_silence_cutpoints("nonexistent.wav", 0.0), [0.0])
        self.assertEqual(detect_silence_cutpoints("nonexistent.wav", -10.0), [0.0])

    def test_detect_silence_cutpoints_fallback_intervals(self) -> None:
        # When ffmpeg fails or file doesn't exist, it falls back to fixed intervals
        cutpoints = detect_silence_cutpoints("nonexistent_path_fallback.wav", 750.0, chunk_duration=300.0)
        self.assertEqual(cutpoints, [0.0, 300.0, 600.0, 750.0])

    def test_stitch_and_align_segments_single_chunk(self) -> None:
        chunks_data = [
            {
                "chunk_index": 0,
                "slice_start": 0.0,
                "authoritative_start": 0.0,
                "authoritative_end": 100.0,
                "raw_segments": [
                    {"start": 1.0, "end": 5.0, "speaker": "1", "text": "Hello world"},
                    {"start": 6.0, "end": 10.0, "speaker": "Speaker 2", "text": "Hi Alice"},
                ],
            }
        ]
        stitched = stitch_and_align_segments(chunks_data)
        self.assertEqual(len(stitched), 2)
        self.assertEqual(stitched[0]["speaker"], "Speaker 1")
        self.assertEqual(stitched[0]["start"], 1.0)
        self.assertEqual(stitched[0]["end"], 5.0)
        self.assertEqual(stitched[1]["speaker"], "Speaker 2")

    def test_stitch_and_align_segments_multi_chunk_remapping(self) -> None:
        chunks_data = [
            {
                "chunk_index": 0,
                "slice_start": 0.0,
                "authoritative_start": 0.0,
                "authoritative_end": 100.0,
                "raw_segments": [
                    {"start": 80.0, "end": 95.0, "speaker": "Speaker 1", "text": "Concluding chunk 0"},
                ],
            },
            {
                "chunk_index": 1,
                "slice_start": 85.0,
                "authoritative_start": 100.0,
                "authoritative_end": 200.0,
                "raw_segments": [
                    # Overlap segment matching Speaker 1 in chunk 0
                    {"start": 0.0, "end": 10.0, "speaker": "Local_A", "text": "Concluding chunk 0 overlap"},
                    # Authoritative segment by Local_A (should be remapped to Speaker 1)
                    {"start": 20.0, "end": 30.0, "speaker": "Local_A", "text": "Continuing in chunk 1"},
                    # New speaker in chunk 1
                    {"start": 35.0, "end": 45.0, "speaker": "Local_B", "text": "New speaker chiming in"},
                ],
            },
        ]
        stitched = stitch_and_align_segments(chunks_data, overlap_seconds=15.0)
        # Chunk 0 has 1 segment, Chunk 1 has 2 authoritative segments (mid >= 100.0)
        self.assertEqual(len(stitched), 3)
        self.assertEqual(stitched[0]["speaker"], "Speaker 1")
        # Second segment is at slice_start=85 + 20 = 105.0 -> mid 110.0 >= 100.0
        self.assertEqual(stitched[1]["start"], 105.0)
        self.assertEqual(stitched[1]["speaker"], "Speaker 1")  # remapped from Local_A
        # Third segment is at slice_start=85 + 35 = 120.0
        self.assertEqual(stitched[2]["start"], 120.0)
        self.assertEqual(stitched[2]["speaker"], "Speaker 2")  # Local_B assigned new Speaker 2


if __name__ == "__main__":
    unittest.main()
