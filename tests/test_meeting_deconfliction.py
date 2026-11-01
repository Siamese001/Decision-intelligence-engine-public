"""
tests/test_meeting_deconfliction.py

Unit tests for Governed Meeting Deconfliction & Deduplication Engine.
"""
from __future__ import annotations

import unittest
from datetime import time

from dual_pipeline.meeting_deconfliction import (
    MeetingCollisionDetector,
    MeetingCollisionResult,
    MeetingDeconflictionError,
    compute_attendee_overlap,
    compute_discourse_similarity,
    compute_temporal_overlap,
    extract_discourse_fingerprint,
    parse_time_window,
)


class TestMeetingDeconflictionEngine(unittest.TestCase):
    """Test suite certifying the Meeting Deconfliction Engine."""

    def test_parse_time_window_various_formats(self) -> None:
        # Alphanumeric with underscore
        t1, t2 = parse_time_window("1018AM_1030AM_ET")
        self.assertEqual(t1, time(10, 18))
        self.assertEqual(t2, time(10, 30))

        # Standard display format with colon
        t1, t2 = parse_time_window("10:15 AM - 10:30 AM ET")
        self.assertEqual(t1, time(10, 15))
        self.assertEqual(t2, time(10, 30))

        # Afternoon PM format
        t1, t2 = parse_time_window("01:37 PM - 02:10 PM ET")
        self.assertEqual(t1, time(13, 37))
        self.assertEqual(t2, time(14, 10))

        # Invalid/empty
        self.assertEqual(parse_time_window(None), (None, None))
        self.assertEqual(parse_time_window(""), (None, None))

    def test_compute_temporal_overlap(self) -> None:
        # Direct overlap: 10:18-10:30 vs 10:15-10:30
        w1 = (time(10, 18), time(10, 30))
        w2 = (time(10, 15), time(10, 30))
        score, reason = compute_temporal_overlap(w1, w2)
        self.assertGreaterEqual(score, 0.70)
        self.assertIn("temporal overlap", reason.lower())

        # Distant non-overlap: 10:00-10:15 vs 14:00-14:30
        w3 = (time(14, 0), time(14, 30))
        score, reason = compute_temporal_overlap(w1, w3)
        self.assertEqual(score, 0.0)
        self.assertIsNone(reason)

    def test_extract_discourse_fingerprint(self) -> None:
        sample = (
            "Taylor Mercer and Reid Schenk aligned on $12M capital savings across 40 sites. "
            "Matt Martinello and Raisa Weiser manage financial endorsements through the governance app. "
            "Chris chairs the Wednesday Go/No-Go gate."
        )
        tokens = extract_discourse_fingerprint(sample)
        self.assertIn("reid", tokens)
        self.assertIn("traci", tokens)
        self.assertIn("$12m", tokens)
        self.assertIn("martinello", tokens)
        self.assertIn("weiser", tokens)
        self.assertIn("governance", tokens)

    def test_compute_discourse_similarity(self) -> None:
        text_a = (
            "Reid Schenk and Taylor Mercer discussed financial endorsement app with Matt Martinello. "
            "Raisa Weiser is out of office. Capital savings estimated at $12,000,000 for 40 sites by 2030."
        )
        text_b = (
            "Taylor Mercer aligned with Reid Schenk on $12M capital savings. "
            "Matt Martinello leads product finance where Raisa Weiser endorses charters. Go/No-Go with Chris."
        )
        score, reasons = compute_discourse_similarity(text_a, text_b)
        self.assertGreaterEqual(score, 0.50)
        self.assertTrue(len(reasons) > 0)
        self.assertIn("discourse and entity similarity", reasons[0].lower())

        # Distinct discourse
        unrelated = (
            "Figma slide design review for executive presentations using Inter 42px font. "
            "Color tokens and glassmorphism charts for Q1 roadmap."
        )
        score_diff, _ = compute_discourse_similarity(text_a, unrelated)
        self.assertLess(score_diff, 0.15)

    def test_compute_attendee_overlap(self) -> None:
        att_a = ["Reid Schenk (Director of Product)", "Amit Ayer (Lead Architect)"]
        att_b = {
            "PartnerAdvisory": ["Taylor Mercer", "Quinn Mercer", "Amit Ayer"],
            "Apex Global Enterprises": ["Reid Schenk"],
        }
        score, reason = compute_attendee_overlap(att_a, att_b)
        self.assertGreaterEqual(score, 0.40)
        self.assertIn("Reid", reason)

    def test_detect_collision_real_nexus_0914_scenario(self) -> None:
        """Simulate candidate 'Nexus_Reid_NEA' attempting ingestion against existing 'Nexus_Scope'."""
        detector = MeetingCollisionDetector()
        candidate_text = (
            "Reid Schenk and Amit Ayer discussed labor hour validation with Dan and Sharon. "
            "Taylor Mercer noted $12 million in capital savings across 40 sites. "
            "Matt Martinello and Raisa Weiser handle financial endorsements in the app. "
            "Chris Go/No-Go funding review on Wednesday."
        )
        result = detector.check_candidate_collision(
            project="project_nexus",
            date_str="10-02-2026",
            candidate_slug="Nexus_Reid_NEA",
            candidate_time="10:00 AM - 10:15 AM ET",
            candidate_attendees=["Reid Schenk", "Amit Ayer"],
            candidate_text=candidate_text,
        )
        self.assertTrue(result.is_collision)
        self.assertGreaterEqual(result.confidence, 0.70)
        self.assertEqual(result.existing_slug, "Nexus_Scope")
        self.assertTrue(any("similarity" in r.lower() for r in result.reasons))

    def test_non_colliding_distinct_meetings_on_same_date(self) -> None:
        """Verify that distinct meetings on 09-15-2026 do NOT trigger false collisions."""
        detector = MeetingCollisionDetector()
        # Candidate is Phase 1 technical designs
        candidate_text = (
            "Jen Pan and Amit Ayer reviewed AWS infrastructure architecture, event-driven lambdas, "
            "and PostgreSQL database sizing for Phase 1 Project Beacon telemetry pipeline."
        )
        result = detector.check_candidate_collision(
            project="project_lens",
            date_str="09-15-2026",
            candidate_slug="New_Technical_Architecture_Sync",
            candidate_time="02:00 PM - 02:30 PM ET",
            candidate_attendees=["Jen Pan", "Amit Ayer"],
            candidate_text=candidate_text,
        )
        # SOW Boundaries was Mandy & PartnerAdvisory in the morning (11:00 AM) discussing commercial scope
        # Should NOT collide with SOW Boundaries
        if result.is_collision:
            self.assertNotEqual(result.existing_slug, "SOW_Boundaries_Mandy_PartnerAdvisory")

    def test_meeting_deconfliction_error_formatting(self) -> None:
        res = MeetingCollisionResult(
            is_collision=True,
            confidence=0.85,
            candidate_slug="Nexus_Reid_NEA",
            date_display="09/14/2026",
            existing_slug="Nexus_Scope",
            reasons=["Direct temporal overlap of ~12m", "High discourse similarity (78.5%)"],
        )
        err = MeetingDeconflictionError(res)
        err_str = str(err)
        self.assertIn("[DUPLICATE_MEETING_COLLISION]", err_str)
        self.assertIn("Nexus_Reid_NEA", err_str)
        self.assertIn("Nexus_Scope", err_str)
        self.assertIn("85.0%", err_str)
        self.assertIn("--allow-duplicate", err_str)
        self.assertIn("--augment-existing", err_str)


if __name__ == "__main__":
    unittest.main()
