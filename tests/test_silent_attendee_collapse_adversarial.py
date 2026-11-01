"""
tests/test_silent_attendee_collapse_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 2:
Silent Attendee Decoupling, Diarization Collapse Gating & Malformed Hints.

Categories Covered:
1. Boundary Payload: Empty hints file, 0-turn audio, zero-length speaker names.
2. Malformed Input: Corrupted JSON in speaker_hints.json, truncated RTTM records, invalid UTF-8.
3. Invariant Bypass Attempt: Single acoustic cluster in multi-party meeting fails closed; 90% skew fails closed.
4. Exact Production Regression: Multi-person roster with silent observers parses cleanly without false-positive collapse error.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    load_diarization_tracks,
    load_speaker_hints,
    score_candidates_for_speaker,
)


class TestSilentAttendeeCollapseAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_wave2_adv_")
        self.temp_path = Path(self.temp_dir)
        self.engine = DiarizationEngine(lazy=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payload
    # -------------------------------------------------------------------------
    def test_boundary_payload_empty_hints_and_zero_turns(self):
        """Empty hints file and zero-line RTTM behave safely without unhandled crashes."""
        empty_hints = self.temp_path / "speaker_hints.json"
        empty_hints.write_text("[]", encoding="utf-8")

        hints = load_speaker_hints({"speaker_hints_path": str(empty_hints)})
        self.assertEqual(hints, [])

        with self.assertRaises(RuntimeError) as ctx:
            self.engine.parse_rttm_turns(
                rttm_lines=[],
                source_name="empty_meeting.wav",
                expected_roster_count=3,
            )
        self.assertIn("0 speaker turns", str(ctx.exception))

    def test_boundary_payload_single_speaker_roster_does_not_collapse(self):
        """Single-participant meeting with 1 cluster is legitimate solo turn, not a collapse."""
        rttm = [
            "SPEAKER audio 1 0.00 10.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 12.00 5.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
        ]
        turns = self.engine.parse_rttm_turns(
            rttm_lines=rttm,
            source_name="solo_meeting.wav",
            expected_roster_count=1,
            max_single_speaker_collapse_pct=0.85,
        )
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "SPEAKER_00")

    # -------------------------------------------------------------------------
    # Category 2: Malformed Input
    # -------------------------------------------------------------------------
    def test_malformed_input_corrupt_json_in_speaker_hints(self):
        """Corrupt or truncated JSON in speaker_hints.json is gracefully handled as empty list."""
        corrupt_hints = self.temp_path / "speaker_hints.json"
        corrupt_hints.write_text("{invalid json: [truncated", encoding="utf-8")

        hints = load_speaker_hints({"speaker_hints_path": str(corrupt_hints)})
        self.assertEqual(hints, [])

    def test_malformed_input_corrupted_rttm_lines(self):
        """Malformed or non-SPEAKER RTTM lines are ignored without poisoning turn sequence."""
        rttm = [
            "COMMENT this is a non-speaker header line",
            "MALFORMED_LINE missing tokens",
            "SPEAKER audio 1 0.00 4.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 5.00 4.00 <NA> <NA> SPEAKER_01 <NA> <NA>",
        ]
        turns = self.engine.parse_rttm_turns(
            rttm_lines=rttm,
            source_name="corrupted_rttm.wav",
            expected_roster_count=2,
        )
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "SPEAKER_00")
        self.assertEqual(turns[1]["speaker"], "SPEAKER_01")

    # -------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempt
    # -------------------------------------------------------------------------
    def test_invariant_bypass_single_acoustic_cluster_fails_closed(self):
        """True single-speaker collapse (only 1 cluster for 4 expected attendees) fails closed."""
        rttm = [
            "SPEAKER audio 1 0.00 10.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 12.00 15.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 30.00 10.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
        ]
        with self.assertRaises(DiarizationCollapseError) as ctx:
            self.engine.parse_rttm_turns(
                rttm_lines=rttm,
                source_name="collapsed_meeting.wav",
                expected_roster_count=4,
                max_single_speaker_collapse_pct=0.85,
            )
        self.assertIn("Found only 1 acoustic cluster", str(ctx.exception))

    def test_invariant_bypass_excessive_skew_fails_closed(self):
        """Excessive single-speaker skew (>85% duration) fails closed with DiarizationCollapseError."""
        rttm = [
            "SPEAKER audio 1 0.00 95.00 <NA> <NA> SPEAKER_00 <NA> <NA>",  # 95% of time
            "SPEAKER audio 1 96.00 5.00 <NA> <NA> SPEAKER_01 <NA> <NA>",   # 5% of time
        ]
        with self.assertRaises(DiarizationCollapseError) as ctx:
            self.engine.parse_rttm_turns(
                rttm_lines=rttm,
                source_name="skewed_meeting.wav",
                expected_roster_count=2,
                max_single_speaker_collapse_pct=0.85,
            )
        self.assertIn("Single speaker collapse detected", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Category 4: Exact Production Regression
    # -------------------------------------------------------------------------
    def test_exact_production_regression_multi_person_roster_with_silent_observers(self):
        """Regression: 4 roster participants with 2 silent observers must succeed without DiarizationCollapseError."""
        rttm = [
            "SPEAKER audio 1 0.00 20.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 22.00 20.00 <NA> <NA> SPEAKER_01 <NA> <NA>",
            "SPEAKER audio 1 45.00 20.00 <NA> <NA> SPEAKER_00 <NA> <NA>",
            "SPEAKER audio 1 68.00 20.00 <NA> <NA> SPEAKER_01 <NA> <NA>",
        ]
        # Previously failed: len(clusters) < expected_roster_count (2 < 4)
        turns = self.engine.parse_rttm_turns(
            rttm_lines=rttm,
            source_name="client_standup_with_observers.wav",
            expected_roster_count=4,
            max_single_speaker_collapse_pct=0.85,
        )
        self.assertEqual(len(turns), 4)
        self.assertEqual(len(set(t["speaker"] for t in turns)), 2)


if __name__ == "__main__":
    unittest.main()
