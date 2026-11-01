"""
tests/test_mlx_whisper_enhancements.py

Test Suite for Wave 2 & Wave 3:
1. Domain initial_prompt construction from attendee roster, project, and domain lexicon.
2. Anti-hallucination phrase filtering and prompt echo suppression.
3. Autoregressive loop detection and consecutive duplicate phrase collapsing.
4. Adaptive multi-speaker turn clustering replacing binary flip-flop.
5. Rule C-11 unannotated gap (>180s) detection and notation injection.
6. Extraction of acoustic ambiguity items for the Ambiguity Lock Register (ALR).
"""
from __future__ import annotations

import unittest

from dual_pipeline.audio_postprocess import (
    build_initial_prompt,
    collapse_duplicate_phrases,
    has_runaway_loop,
    is_prompt_echo,
    sanitize_transcript_text,
)
from dual_pipeline.turn_clusterer import (
    _format_seconds_to_timestamp,
    cluster_conversational_turns,
)


class TestMLXWhisperEnhancements(unittest.TestCase):
    def test_build_initial_prompt_comprehensive(self):
        metadata = {
            "roster": [
                {"name": "Alice Smith", "role": "Architect", "org": "PartnerAdvisory"},
                {"name": "Bob Jones", "role": "Lead", "org": "Apex Global"},
                {"name": "Charlie Brown", "role": "Engineer", "org": "APEX"},
            ],
            "project_name": "Project_Nexus",
            "meeting_name": "Architecture_Sync",
            "domain_lexicon": {
                "agrivoltaics": "Solar farming",
                "LAMS": "Land Asset Management System",
                "Unifier": "Oracle Unifier",
            },
        }

        prompt = build_initial_prompt(metadata)
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertIn("Alice Smith", prompt)
        self.assertIn("Bob Jones", prompt)
        self.assertIn("Project: Project_Nexus", prompt)
        self.assertIn("Apex Global", prompt)
        self.assertIn("agrivoltaics", prompt)
        self.assertLessEqual(len(prompt), 900)

    def test_build_initial_prompt_empty(self):
        self.assertIsNone(build_initial_prompt(None))
        self.assertIsNone(build_initial_prompt({}))

    def test_is_prompt_echo(self):
        prompt = "Meeting participants: Alice, Bob. Project: Nexus. Keywords: Apex Global, APEX, AWS, Snowflake."
        echo1 = "Meeting participants: Alice, Bob. Project: Nexus. Keywords: Apex Global, APEX, AWS, Snowflake."
        self.assertTrue(is_prompt_echo(echo1, prompt))

        echo2 = "participants alice bob project nexus keywords"
        self.assertTrue(is_prompt_echo(echo2, prompt))

        real_speech = "We need to finalize the schema migration for Snowflake by next Tuesday."
        self.assertFalse(is_prompt_echo(real_speech, prompt))

    def test_hallucination_filtering(self):
        # Known Whisper silence hallucinations must be discarded
        for h in ("Thank you for watching", "Thanks for listening.", "Subtitles by www.opensubtitles.org", "The end."):
            res = sanitize_transcript_text(h)
            self.assertEqual(res, "", f"Expected '{h}' to be filtered out")

        # Legitimate speech must be preserved
        valid = "Thank you for sending over the architecture documentation, Bob."
        res_valid = sanitize_transcript_text(valid)
        self.assertEqual(res_valid, valid)

    def test_repetition_loop_detection_and_collapsing(self):
        # Runaway loop must be suppressed
        runaway = "I think I think I think I think I think I think I think I think"
        self.assertTrue(has_runaway_loop(runaway.split()))
        self.assertEqual(sanitize_transcript_text(runaway), "")

        # Minor stuttering must be collapsed cleanly
        stutter = ["We", "should", "proceed", "We", "should", "proceed", "with", "Wave", "1"]
        collapsed = collapse_duplicate_phrases(stutter)
        self.assertEqual(collapsed, ["We", "should", "proceed", "with", "Wave", "1"])

    def test_cluster_conversational_turns_multi_speaker(self):
        metadata = {
            "roster": ["Alice", "Bob", "Charlie", "David"]
        }
        segments = [
            {"start": 0.0, "end": 4.0, "text": "Hello everyone, let's start the meeting."},
            {"start": 6.0, "end": 10.0, "text": "Thanks Alice, Bob here. I reviewed the SOW."},
            {"start": 12.0, "end": 16.0, "text": "Charlie here, what is the status of the Snowflake pipeline?"},
        ]

        clustered, ambiguities = cluster_conversational_turns(segments, metadata=metadata)
        self.assertEqual(len(clustered), 3)
        self.assertEqual(clustered[0]["speaker"], "Speaker 1")
        self.assertEqual(clustered[1]["speaker"], "Speaker 2")
        self.assertEqual(clustered[2]["speaker"], "Speaker 3")

    def test_cluster_conversational_turns_recording_gap_c11(self):
        segments = [
            {"start": 10.0, "end": 20.0, "text": "Initial discussion on turbine limits."},
            # 220-second gap (> 180s threshold)
            {"start": 240.0, "end": 250.0, "text": "Resuming discussion after the break."},
        ]

        clustered, _ = cluster_conversational_turns(segments)
        # Should contain gap annotation segment between segment 0 and 1
        gap_segments = [s for s in clustered if s.get("is_gap_annotation")]
        self.assertEqual(len(gap_segments), 1)
        gap_text = gap_segments[0]["text"]
        self.assertIn("[Recording gap:", gap_text)
        self.assertIn("00:00:20", gap_text)
        self.assertIn("00:04:00", gap_text)

    def test_acoustic_ambiguity_extraction(self):
        segments = [
            {"start": 10.0, "end": 15.0, "text": "Standard clear speech with high confidence.", "avg_logprob": -0.2},
            {"start": 20.0, "end": 25.0, "text": "Muffled inaudible technical term garbled", "avg_logprob": -1.2, "no_speech_prob": 0.7},
        ]

        _, ambiguities = cluster_conversational_turns(segments)
        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(ambiguities[0]["timestamp"], "00:00:20")
        self.assertIn("Muffled", ambiguities[0]["term"])
        self.assertIn("Acoustic ambiguity", ambiguities[0]["status"])

    def test_format_seconds_to_timestamp(self):
        self.assertEqual(_format_seconds_to_timestamp(0.0), "00:00:00")
        self.assertEqual(_format_seconds_to_timestamp(65.5), "00:01:05")
        self.assertEqual(_format_seconds_to_timestamp(3665.0), "01:01:05")


if __name__ == "__main__":
    unittest.main()
