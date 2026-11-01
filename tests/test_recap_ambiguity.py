"""Tests for Output 2 Executive Recap Ambiguity Detection Mesh (Domains E1-E4)."""
from __future__ import annotations

import unittest

from dual_pipeline.recap_ambiguity_core import (
    DetectorE1Roster,
    DetectorE2Altitude,
    DetectorE3Actions,
    DetectorE4Systems,
    ExecutiveAmbiguityDomain,
    ExecutiveAmbiguityEngine,
    ExecutiveAmbiguityRecord,
    ExecutiveCandidateOption,
)


class TestRecapAmbiguityCore(unittest.TestCase):

    def test_executive_candidate_and_record_margin(self):
        opt1 = ExecutiveCandidateOption("k1", "Label 1", "Desc 1", 0.55, "Rat 1")
        opt2 = ExecutiveCandidateOption("k2", "Label 2", "Desc 2", 0.45, "Rat 2")
        rec = ExecutiveAmbiguityRecord(
            ambiguity_id="rec-1",
            domain=ExecutiveAmbiguityDomain.E1_AFFILIATION_ROSTER,
            title="Test Ambiguity",
            context_snippet="Test snippet",
            candidates=[opt1, opt2],
        )
        self.assertEqual(rec.margin_delta, 0.10)
        self.assertTrue(rec.is_gate_eligible(margin_threshold=0.20))
        self.assertFalse(rec.is_gate_eligible(margin_threshold=0.05))

        rec.resolve("k1", rationale="Operator selected PartnerAdvisory", timestamp_iso="2026-09-13T12:00:00Z")
        self.assertEqual(rec.status, "RESOLVED")
        self.assertEqual(rec.selected_key, "k1")
        self.assertIsNotNone(rec.selected_option)
        self.assertFalse(rec.is_gate_eligible(margin_threshold=0.20))

    def test_detector_e1_roster(self):
        detector = DetectorE1Roster()
        metadata = {
            "attendees": [
                {"name": "Alex Rivera", "organization": "PartnerAdvisory"},
                {"name": "Jane Doe", "organization": "Unknown", "email": "jdoe@enterprise.example.com"},
            ]
        }
        recs = detector.detect(metadata)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].domain, ExecutiveAmbiguityDomain.E1_AFFILIATION_ROSTER)
        self.assertIn("Jane Doe", recs[0].title)

    def test_detector_e2_altitude(self):
        detector = DetectorE2Altitude()
        metadata = {"meeting_name": "Quarterly Steering", "meeting_objective": "General catch-up call"}
        recs = detector.detect(metadata)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].domain, ExecutiveAmbiguityDomain.E2_VALUE_ALTITUDE)

        # When catalyst is present, no ambiguity surfaced
        metadata_ok = {"meeting_name": "Quarterly Steering", "meeting_objective": "Scale developer throughput against flat headcount"}
        recs_ok = detector.detect(metadata_ok)
        self.assertEqual(len(recs_ok), 0)

    def test_detector_e3_actions(self):
        detector = DetectorE3Actions()
        action_items = [
            {
                "action": "Discuss architecture options with team",
                "owner": "Team",
                "due_date": "Next week",
            },
            {
                "action": "Finalize and submit API contracts",
                "owner": "John Smith",
                "due_date": "09/30/2026",
            },
        ]
        recs = detector.detect_from_items(action_items)
        # Should flag passive verb ("Discuss"), unassigned owner ("Team"), and bad date ("Next week")
        self.assertGreaterEqual(len(recs), 3)
        domains = [r.domain for r in recs]
        self.assertTrue(all(d == ExecutiveAmbiguityDomain.E3_ACTION_DRI_DEADLINE for d in domains))

    def test_detector_e4_systems(self):
        detector = DetectorE4Systems()
        text = "The project team is utilizing Unifier and LAMS to manage land and budget without clear handoff."
        recs = detector.detect_from_text(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].domain, ExecutiveAmbiguityDomain.E4_SYSTEMS_GROUNDING)

    def test_executive_ambiguity_engine_end_to_end(self):
        engine = ExecutiveAmbiguityEngine(margin_threshold=0.20, model="gpt-5.6-luna")
        metadata = {
            "meeting_name": "Transmission Planning",
            "meeting_objective": "Review status updates",
            "attendees": [{"name": "Alex Carter", "organization": "TBD"}],
        }
        text = "Discussion around Unifier and LAMS integration."
        action_items = [{"action": "Consider schedule buffer", "owner": "TBC", "due_date": "TBD"}]
        results = engine.scan(metadata, transcript_text=text, action_items=action_items)
        self.assertGreaterEqual(len(results), 4)


if __name__ == "__main__":
    unittest.main()
