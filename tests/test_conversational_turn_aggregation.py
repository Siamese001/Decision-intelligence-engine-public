"""
tests/test_conversational_turn_aggregation.py

Unit tests for Wave 2: Conversational Turn Aggregation & Paragraph Coalescing.
Verifies:
1. ConversationalTurnAggregator coalesces adjacent chunks from the same speaker into coherent turns.
2. ConversationalTurnAggregator flushes immediately on speaker change.
3. ConversationalTurnAggregator flushes on silence gaps.
4. join_speech_fragments seamlessly reconstructs sentences split across 8-second chunk boundaries.
5. coalesce_speaker_turns merges consecutive turns from the same speaker in LiveTranscriptEngine.
6. coalesce_transcript_markdown_turns eliminates 8-second heading spam while preserving pause markers.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
    ConversationalTurnAggregator,
    coalesce_speaker_turns,
    coalesce_transcript_markdown_turns,
    join_speech_fragments,
)


class TestConversationalTurnAggregation(unittest.TestCase):
    """Unit tests for turn aggregation and paragraph coalescing."""

    def test_join_speech_fragments_stitches_mid_sentence_cuts(self) -> None:
        """Trailing ellipsis and cross-chunk clause fragments must join seamlessly."""
        frags = [
            "I'm working on prompting through the pipeline and then Karen has done more of like the API integrations...",
            "between the front end and the back end and then I've done the user interface.",
        ]
        joined = join_speech_fragments(frags)
        self.assertNotIn("...", joined)
        self.assertIn("API integrations between the front end", joined)

    def test_join_speech_fragments_handles_hyphenated_splits(self) -> None:
        frags = ["We are build-", "ing the pipeline."]
        joined = join_speech_fragments(frags)
        self.assertEqual(joined, "We are building the pipeline.")

    def test_conversational_turn_aggregator_coalesces_same_speaker(self) -> None:
        """Adjacent chunks from the same speaker must accumulate without premature heading flush."""
        completed_turns = []
        agg = ConversationalTurnAggregator(
            max_silence_gap_seconds=16,
            max_turn_duration_seconds=48,
            on_turn_completed=lambda t: completed_turns.append(t),
        )

        # Chunk 1 (0s) - Jake Stark speaks
        t1 = agg.add_chunk(0, 0, "Well, I think we have a pretty good view.", "Jake Stark")
        self.assertIsNone(t1)

        # Chunk 2 (8s) - Jake Stark continues speaking
        t2 = agg.add_chunk(1, 8, "We should make sure the architecture is clear.", "Jake Stark")
        self.assertIsNone(t2)

        # Chunk 3 (16s) - Jake Stark concludes his turn
        t3 = agg.add_chunk(2, 16, "That covers the main components for today.", "Jake Stark")
        self.assertIsNone(t3)

        # Now speaker changes to Amit Ayer at 24s -> triggers flush of Jake's accumulated turn
        t_flushed = agg.add_chunk(3, 24, "Agreed, let's review the wedge.", "Amit Ayer")
        self.assertIsNotNone(t_flushed)
        self.assertEqual(t_flushed.speaker, "Jake Stark")
        self.assertEqual(t_flushed.start_seconds, 0)
        self.assertEqual(t_flushed.end_seconds, 24)
        self.assertIn("pretty good view", t_flushed.text)
        self.assertIn("main components for today", t_flushed.text)

    def test_conversational_turn_aggregator_flushes_on_silence_gap(self) -> None:
        """A silence gap > max_silence_gap_seconds flushes the turn."""
        agg = ConversationalTurnAggregator(max_silence_gap_seconds=16)
        agg.add_chunk(0, 0, "Initial thought before pause.", "Dan")

        # Chunk arrives at 24s (24 - 0 = 24s > 16s silence gap)
        t_flushed = agg.add_chunk(3, 24, "Resuming after a long pause.", "Dan")
        self.assertIsNotNone(t_flushed)
        self.assertEqual(t_flushed.speaker, "Dan")
        self.assertIn("Initial thought before pause", t_flushed.text)

    def test_coalesce_speaker_turns_tuples(self) -> None:
        """coalesce_speaker_turns in live_transcript_engine merges adjacent tuples."""
        raw_lines = [
            (1, "Jake Stark", "I'm working on prompting through the pipeline"),
            (2, "Jake Stark", "and Karen has done the API integrations."),
            (3, "Amit Ayer", "That makes total sense."),
            (4, "Amit Ayer", "Let's review the deliverables."),
        ]
        coalesced = coalesce_speaker_turns(raw_lines)
        self.assertEqual(len(coalesced), 2)
        self.assertEqual(coalesced[0][1], "Jake Stark")
        self.assertIn("prompting through the pipeline and Karen", coalesced[0][2])
        self.assertEqual(coalesced[1][1], "Amit Ayer")
        self.assertIn("total sense. Let's review", coalesced[1][2])

    def test_coalesce_transcript_markdown_turns_eliminates_heading_spam(self) -> None:
        """8-second heading spam from the same speaker is coalesced into one heading."""
        input_md = """# Meeting Transcript

## Transcript

### [00:02:48]

**Jake Stark (PartnerAdvisory):** I'm working on prompting through the pipeline and then Karen has done more of like the API integrations between the front end and the back end and then I've done the...

### [00:02:56]

**Jake Stark (PartnerAdvisory):** That's kind of the clean split of the three roles.

### [00:03:04]

**Amit Ayer (PartnerAdvisory):** Understood, that gives clear ownership.
"""
        coalesced_md = coalesce_transcript_markdown_turns(input_md)
        # Should now have only 2 headings: [00:02:48] for Jake and [00:03:04] for Amit
        self.assertIn("### [00:02:48]", coalesced_md)
        self.assertNotIn("### [00:02:56]", coalesced_md)
        self.assertIn("### [00:03:04]", coalesced_md)
        self.assertIn("clean split of the three roles", coalesced_md)

    def test_coalesce_transcript_markdown_turns_preserves_pause_markers(self) -> None:
        """Pause markers between turns must be preserved without swallowing headings."""
        input_md = """## Transcript

### [00:00:16]

**Jake Stark (PartnerAdvisory):** Let's get started.

[Natural pause: 00:00:24 – 00:01:04 (no speech)]

### [00:01:04]

**Jake Stark (PartnerAdvisory):** We can get a better picture here.
"""
        coalesced_md = coalesce_transcript_markdown_turns(input_md)
        self.assertIn("### [00:00:16]", coalesced_md)
        self.assertIn("[Natural pause: 00:00:24 – 00:01:04 (no speech)]", coalesced_md)
        self.assertIn("### [00:01:04]", coalesced_md)


if __name__ == "__main__":
    unittest.main()
