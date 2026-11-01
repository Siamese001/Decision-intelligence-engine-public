"""tests/test_entity_clarification.py

Unit tests for Domain 6: Referenced Entity Clarification Detector,
AmbiguityEngine D6 integration, and Phase 2 Clearance D6 status governance.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ambiguity_core import (
    AmbiguityDomain,
    AmbiguityEngine,
    AmbiguityStatus,
    EntityClarificationDetector,
)
from dual_pipeline.clearance_lifecycle_core import (
    AmbiguityLockRegister,
    Phase2ClearanceCoordinator,
)


class TestEntityClarification(unittest.TestCase):
    """Test suite for Domain 6 Entity Clarification."""

    def test_d6_detector_identifies_unrostered_names(self):
        detector = EntityClarificationDetector(margin_threshold=0.15)
        turns = [
            {"speaker": "Jen Pan", "text": "We were discussing what Ed was saying about global rules."},
            {"speaker": "Jen Pan", "text": "I think Alex's robot had some issues with the 22.3 release."},
            {"speaker": "Jen Pan", "text": "Lester mentioned the threshold could be single digits."},
            {"speaker": "Jen Pan", "text": "Monday we will review with Teams on Sprint 6."},
        ]
        attendees = ["Jen Pan", "Amit Ayer", "harper Hoffman"]

        records = detector.detect(turns, known_attendees=attendees)
        found_tokens = [r.ambiguity_id.split("-")[2] for r in records]

        self.assertIn("ed", found_tokens)
        self.assertIn("alex", found_tokens)
        self.assertIn("lester", found_tokens)
        # "Monday", "Teams", "Sprint" must be suppressed
        self.assertNotIn("monday", found_tokens)
        self.assertNotIn("teams", found_tokens)
        self.assertNotIn("sprint", found_tokens)

    def test_d6_detector_skips_known_roster_attendees(self):
        detector = EntityClarificationDetector(margin_threshold=0.15)
        turns = [
            {"speaker": "Jen Pan", "text": "Amit mentioned the architecture was already validated."},
            {"speaker": "Amit Ayer", "text": "Mandy agreed with the timeline."},
        ]
        attendees = ["Jen Pan", "Amit Ayer", "harper Hoffman"]

        records = detector.detect(turns, known_attendees=attendees)
        self.assertEqual(len(records), 0)

    def test_d6_candidate_ranking_and_hitl_requirement(self):
        detector = EntityClarificationDetector(margin_threshold=0.25)
        turns = [
            {"speaker": "Jen Pan", "text": "Ed told us that rule creation was taking too long."}
        ]
        attendees = ["Jen Pan"]

        records = detector.detect(turns, known_attendees=attendees)
        self.assertEqual(len(records), 1)
        rec = records[0]

        self.assertEqual(rec.domain, AmbiguityDomain.D6_ENTITY_CLARIFICATION)
        self.assertGreaterEqual(len(rec.candidates), 2)
        # Top candidate should be Eduardo Juan More or Ed Moore
        cand_names = [c.text for c in rec.candidates]
        self.assertTrue(any("Eduardo" in name or "Ed Moore" in name for name in cand_names))
        self.assertTrue(rec.requires_hitl)

    def test_ambiguity_engine_includes_d6_domain(self):
        engine = AmbiguityEngine()
        turns = [
            {"speaker": "Jen Pan", "text": "We should check with Clay on why AWS Strands was chosen."}
        ]
        attendees = ["Jen Pan"]

        records = engine.scan(turns, known_attendees=attendees)
        d6_records = [r for r in records if r.domain == AmbiguityDomain.D6_ENTITY_CLARIFICATION]
        self.assertGreaterEqual(len(d6_records), 1)
        self.assertIn("clay", d6_records[0].ambiguity_id)

    def test_phase2_clearance_unhandled_d6_marks_pending_operator(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_file = Path(tmp_dir) / "test_ledger.json"
            register = AmbiguityLockRegister()
            coordinator = Phase2ClearanceCoordinator(
                register=register,
                ledger_path=ledger_file,
                hitl_callback=None,  # No interactive callback provided
                fail_closed_on_unhandled=False,
            )

            detector = EntityClarificationDetector()
            turns = [{"speaker": "Jen Pan", "text": "What Ed was saying about global rules."}]
            records = detector.detect(turns, known_attendees=["Jen Pan"])
            self.assertEqual(len(records), 1)

            # Append to register
            register.append_lock(records[0])

            cleared = coordinator.clear_queue(records)
            self.assertEqual(len(cleared), 1)
            # D6 should NOT be silently resolved as EXPLICIT, but marked UNRESOLVED_PENDING_OPERATOR
            self.assertEqual(cleared[0].status, AmbiguityStatus.UNRESOLVED_PENDING_OPERATOR)


if __name__ == "__main__":
    unittest.main()
