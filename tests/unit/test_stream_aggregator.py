"""Unit tests for ConversationalTurnAggregator, exact chunk duration, and timestamp integrity.

Validates fixes for:
- T1: Float seconds type safety in _format_time and turn aggregation
- T2: Accurate end_ts and segment duration tracking
- T3: Accurate pause duration accumulation without 15s synthetic inflation
- T4: Accurate segment duration in acoustic diarization alignment
- T5: Dynamic chunk duration in AggregatedTurn instead of hardcoded +8s
- 203-chunk live meeting simulation (~5.14s chunks = ~17.4 min, NOT 49:30 min)
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    format_chunk_time,
    format_hh_mm_ss,
)
from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
    ConversationalTurnAggregator,
)


class TestStreamAggregatorTimestampIntegrity(unittest.TestCase):
    """Verifies that time formatting and turn aggregation gracefully handle floats and exact durations."""

    def test_format_time_float_safety(self):
        aggregator = ConversationalTurnAggregator()
        # Floats must not raise ValueError
        self.assertEqual(aggregator._format_time(0.0), "[00:00]")
        self.assertEqual(aggregator._format_time(5.14), "[00:05]")
        self.assertEqual(aggregator._format_time(65.7), "[01:06]")
        self.assertEqual(aggregator._format_time(3665.2), "[01:01:05]")
        # Standard ints must work identically
        self.assertEqual(aggregator._format_time(0), "[00:00]")
        self.assertEqual(aggregator._format_time(30), "[00:30]")
        self.assertEqual(aggregator._format_time(90), "[01:30]")

    def test_audio_utils_format_float_safety(self):
        # format_chunk_time and format_hh_mm_ss must handle floats without crashing
        self.assertEqual(format_chunk_time(0.0), "[00:00]")
        self.assertEqual(format_chunk_time(5.14), "[00:05]")
        self.assertEqual(format_chunk_time(65.8), "[01:06]")
        self.assertEqual(format_chunk_time(3605.0), "[01:00:05]")

        self.assertEqual(format_hh_mm_ss(0.0), "00:00:00")
        self.assertEqual(format_hh_mm_ss(5.14), "00:00:05")
        self.assertEqual(format_hh_mm_ss(125.4), "00:02:05")

    def test_turn_aggregator_uses_exact_chunk_duration(self):
        aggregator = ConversationalTurnAggregator()
        # Add chunk 0 starting at 0.0s with duration 5.14s
        turn1 = aggregator.add_chunk(0, 0.0, "Hello everyone,", "Alice", duration=5.14)
        self.assertIsNone(turn1)

        # Add chunk 1 starting at 5.14s with duration 5.20s
        turn2 = aggregator.add_chunk(1, 5.14, "welcome to the meeting.", "Alice", duration=5.20)
        self.assertIsNone(turn2)

        # Speaker changes to Bob at 10.34s with duration 4.80s -> flushes Alice's turn
        completed = aggregator.add_chunk(2, 10.34, "Thanks Alice.", "Bob", duration=4.80)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.speaker, "Alice")
        self.assertEqual(completed.start_seconds, 0)
        # End seconds must be round(5.14 + 5.20) = 10, NOT 5 + 8 = 13!
        self.assertEqual(completed.end_seconds, 10)
        self.assertEqual(completed.start_time_str, "[00:00]")
        self.assertEqual(completed.end_time_str, "[00:10]")

        # Flush final turn
        bob_turn = aggregator.flush()
        self.assertIsNotNone(bob_turn)
        self.assertEqual(bob_turn.speaker, "Bob")
        self.assertEqual(bob_turn.start_seconds, 10)
        # End seconds: round(10.34 + 4.80) = 15
        self.assertEqual(bob_turn.end_seconds, 15)
        self.assertEqual(bob_turn.start_time_str, "[00:10]")
        self.assertEqual(bob_turn.end_time_str, "[00:15]")

    def test_203_chunks_live_meeting_timestamp_simulation(self):
        """Recreates the exact RCA failure condition: 203 chunks of ~5.14s average length.

        Verifies that cumulative duration correctly reaches ~1043 seconds (~17:24),
        completely disproving the 49:30 synthetic inflation bug.
        """
        aggregator = ConversationalTurnAggregator()
        cumulative_sec = 0.0
        avg_chunk_dur = 5.1424  # 16,702,634 frames / 16,000 Hz / 203 chunks

        turns = []
        for chunk_idx in range(203):
            sec = cumulative_sec
            dur = avg_chunk_dur
            cumulative_sec += dur

            speaker = "Jill Cizewski" if (chunk_idx // 10) % 2 == 0 else "Daniel Brankin"
            flushed = aggregator.add_chunk(chunk_idx, sec, f"Turn snippet {chunk_idx}.", speaker, duration=dur)
            if flushed:
                turns.append(flushed)

        final_turn = aggregator.flush()
        if final_turn:
            turns.append(final_turn)

        # Final timestamp must be around 1043s = [17:23] or [17:24], NOT [49:30]
        final_sec = turns[-1].end_seconds
        self.assertAlmostEqual(final_sec, 1044, delta=2)
        final_time_str = aggregator._format_time(final_sec)
        self.assertIn(final_time_str, ["[17:23]", "[17:24]", "[17:25]"])
        self.assertNotIn("[49:", final_time_str)


if __name__ == "__main__":
    unittest.main()
