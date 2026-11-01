"""Unit tests for Wave 3: Live recorder diarization support, turn clustering, and roster resolution."""
from __future__ import annotations

import contextlib
import shutil
import tempfile
import unittest
from typing import Any

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
    ConversationalSpeakerTracker,
)
from dual_pipeline.turn_clusterer import _is_continuation, cluster_conversational_turns


class TestLiveRecorderDiarization(unittest.TestCase):
    """Unit tests for speaker tracking, turn clustering, and post-recording roster resolution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_diar_")
        self._recorders: list[Any] = []

    def tearDown(self) -> None:
        for rec in getattr(self, "_recorders", []):
            with contextlib.suppress(Exception):
                rec._release_slug_lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_speaker_tracker_adopts_acoustic_speaker(self) -> None:
        """Verify that ConversationalSpeakerTracker adopts acoustic_speaker if no higher priority lexical cue exists."""
        tracker = ConversationalSpeakerTracker(
            attendees=[{"name": "Alice"}, {"name": "Bob"}],
            primary_speaker="Alice",
        )
        # Generic phrase with acoustic speaker provided
        speaker = tracker.attribute_chunk("We should review the pipeline architecture.", acoustic_speaker="Bob")
        self.assertEqual(speaker, "Bob")

    def test_speaker_tracker_vocative_overrides_acoustic_speaker(self) -> None:
        """Verify that a strong vocative cue takes precedence over raw acoustic speaker."""
        tracker = ConversationalSpeakerTracker(
            attendees=[{"name": "Alice"}, {"name": "Bob"}],
            primary_speaker="Alice",
        )
        # Alice addressing Bob
        speaker = tracker.attribute_chunk("Bob, what do you think?", acoustic_speaker="Bob")
        self.assertEqual(speaker, "Alice")  # Because Alice is speaking to Bob

    def test_is_continuation_detection(self) -> None:
        """Verify that _is_continuation detects lexical sentence continuations across pauses."""
        self.assertTrue(_is_continuation("We need to decide on the architecture,", "and finalize the scope."))
        self.assertTrue(_is_continuation("The timeline is tight—", "however we can deliver."))
        self.assertTrue(_is_continuation("We are looking at...", "three distinct options."))
        self.assertTrue(_is_continuation("First milestone is complete", "and verified with tests."))
        # New sentence starting with capital letter
        self.assertFalse(_is_continuation("First milestone is complete.", "Next we will discuss budget."))

    def test_turn_clusterer_preserves_speaker_on_continuation(self) -> None:
        """Verify that cluster_conversational_turns does not rotate speaker across a pause if text continues sentence."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "We need to verify the credentials,"},
            {"start": 4.5, "end": 6.5, "text": "and ensure the tokens are propagating."},  # 2.5s pause, but continuation
        ]
        clustered, _ = cluster_conversational_turns(segments, pause_threshold=1.6)
        self.assertEqual(len(clustered), 2)
        self.assertEqual(clustered[0]["speaker"], "Speaker 1")
        self.assertEqual(clustered[1]["speaker"], "Speaker 1")

    def test_turn_clusterer_switches_speaker_on_new_sentence_after_pause(self) -> None:
        """Verify that cluster_conversational_turns rotates speaker when pause exceeds threshold on a new sentence."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "We need to verify the credentials."},
            {"start": 5.0, "end": 7.0, "text": "I agree with that assessment."},  # 3.0s pause, new sentence
        ]
        clustered, _ = cluster_conversational_turns(segments, pause_threshold=1.6)
        self.assertEqual(len(clustered), 2)
        self.assertEqual(clustered[0]["speaker"], "Speaker 1")
        self.assertEqual(clustered[1]["speaker"], "Speaker 2")

    def test_live_preview_attribution_defaults_to_speaker(self) -> None:
        """Verify that live preview attribution defaults to 'Speaker' without hardcoded primary speaker."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Nexus Alignment Jill",
            meeting_date="09/28/2026",
            attendees="Jill Cashen, Amit Ayer",
            output_dir=self.temp_dir,
            mock_mode=True,
        )
        self._recorders.append(recorder)
        self.assertEqual(recorder._default_speaker, "Speaker")

    def test_live_transcript_never_overwritten_by_post_recording_alignment(self) -> None:
        """Verify anti-autonomous attribution invariant: post-stop alignment never rewrites live .md."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Nexus Alignment Jill",
            meeting_date="09/28/2026",
            attendees="Jill Cashen, Amit Ayer",
            output_dir=self.temp_dir,
            mock_mode=True,
        )
        self._recorders.append(recorder)

        recorder.transcript_md.parent.mkdir(parents=True, exist_ok=True)
        original_md = (
            "# Live Meeting Transcript: Nexus Alignment Jill\n\n"
            "**Provenance:** `LIVE_PREVIEW (non-authoritative)`\n\n"
            "**[00:00]** Speaker: Initial live speech turn.\n"
        )
        recorder.transcript_md.write_text(original_md, encoding="utf-8")

        turns = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
            {"speaker": "SPEAKER_01", "start": 10.5, "end": 20.0},
        ]
        # Calling alignment should be a no-op that preserves live .md exactly
        recorder._align_transcript_with_acoustic_turns(turns)
        current_md = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertEqual(current_md, original_md)

    def test_live_transcript_provenance_header(self) -> None:
        """Verify that newly initialized live transcripts declare LIVE_PREVIEW provenance."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Nexus Alignment Jill",
            meeting_date="09/28/2026",
            attendees="Jill Cashen, Amit Ayer",
            output_dir=self.temp_dir,
            mock_mode=True,
        )
        self._recorders.append(recorder)
        recorder._init_transcript_file()
        content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("LIVE_PREVIEW (non-authoritative)", content)


if __name__ == "__main__":
    unittest.main()

