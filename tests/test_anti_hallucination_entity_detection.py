"""tests/test_anti_hallucination_entity_detection.py

Unit and regression tests for Anti-Hallucination Hardening:
- Elimination of ASR phonetic artifacts and zero-evidence phantom stakeholders (C-12).
- Lexical filtering, syntactic validation, and evidence-based candidate gating (C-27).
- Clearance lifecycle quarantine of ungrounded fast-path candidates.
"""

from __future__ import annotations

import unittest

from dual_pipeline.ambiguity_core import (
    EntityClarificationDetector,
    is_suppressed_or_common_word,
    is_valid_name_syntax,
    should_surface_unconfirmed_candidate,
)
from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import execute_clearance_lifecycle


class DummyRecorder:
    """Mock tracing recorder."""
    def span(self, name: str):
        class DummySpan:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return DummySpan()


class TestAntiHallucinationEntityDetection(unittest.TestCase):
    """Test suite validating anti-hallucination guards and regression protections."""

    def test_alarbe_hallucination_produces_zero_records(self):
        """Verifies that 'Last week, what Alarbe is, she had a meeting with Mandy in the afternoon.'
        produces ZERO ambiguity records for Alarbe.
        """
        detector = EntityClarificationDetector()
        turns = [
            {
                "speaker": "Jen Pan",
                "text": "Last week, what Alarbe is, she had a meeting with Mandy in the afternoon.",
            }
        ]
        attendees = ["Jen Pan", "harper Hoffman"]

        records = detector.detect(turns, known_attendees=attendees)
        found_tokens = [r.ambiguity_id.split("-")[2] for r in records]

        self.assertNotIn("alarbe", found_tokens)
        self.assertEqual(len(records), 0)

    def test_common_verbs_produce_zero_records(self):
        """Verifies that 'what happened is...' and 'what started was...' produce ZERO ambiguity records."""
        detector = EntityClarificationDetector()
        turns = [
            {"speaker": "Jen Pan", "text": "what happened is that we agreed to proceed."},
            {"speaker": "Jen Pan", "text": "what Happened is that the rule was reviewed."},
            {"speaker": "Jen Pan", "text": "what started was an exploratory sprint."},
            {"speaker": "Jen Pan", "text": "what Started was the preliminary check."},
        ]
        records = detector.detect(turns, known_attendees=["Jen Pan"])
        found_tokens = [r.ambiguity_id.split("-")[2] for r in records]

        self.assertNotIn("happened", found_tokens)
        self.assertNotIn("started", found_tokens)
        self.assertEqual(len(records), 0)

    def test_legitimate_references_continue_to_be_identified(self):
        """Verifies that legitimate references like 'We were discussing what Ed was saying...'
        or 'I talked with Alex Sanchez' continue to be identified correctly.
        """
        detector = EntityClarificationDetector()
        turns = [
            {"speaker": "Jen Pan", "text": "We were discussing what Ed was saying about global rules."},
            {"speaker": "Jen Pan", "text": "I talked with Alex Sanchez about the integration tests."},
        ]
        attendees = ["Jen Pan"]

        records = detector.detect(turns, known_attendees=attendees)
        found_tokens = [r.ambiguity_id.split("-")[2] for r in records]

        self.assertIn("ed", found_tokens)
        self.assertIn("alex", found_tokens)
        self.assertEqual(len(records), 2)

        # Ed record verification
        ed_rec = next(r for r in records if "ed" in r.ambiguity_id)
        cand_texts = [c.text for c in ed_rec.candidates]
        self.assertTrue(any("Eduardo Juan More" in t for t in cand_texts))

        # Alex record verification
        alex_rec = next(r for r in records if "alex" in r.ambiguity_id)
        alex_cand_texts = [c.text for c in alex_rec.candidates]
        self.assertTrue(any("Alex Sanchez" in t for t in alex_cand_texts))

    def test_is_suppressed_or_common_word_coverage(self):
        """Tests lexical filter suppression of standard English verbs, adjectives, and pronouns."""
        verbs = ["alarmed", "started", "happened", "follows", "means", "knows", "makes", "takes", "decided", "discussed"]
        for v in verbs:
            self.assertTrue(is_suppressed_or_common_word(v))
            self.assertTrue(is_suppressed_or_common_word(v.title()))
            self.assertTrue(is_suppressed_or_common_word(v.upper()))

        # Real entity names must not be suppressed
        legit_names = ["Alex", "Ed", "Miles", "Anthony", "Clay", "Josh", "Lester", "Morgan Vance"]
        for name in legit_names:
            self.assertFalse(is_suppressed_or_common_word(name))

    def test_is_valid_name_syntax(self):
        """Tests syntax validation for prepositions and communicative verbs."""
        turn_alarbe = "Last week, what Alarbe is, she had a meeting with Mandy in the afternoon."
        # 'what' as preposition trigger must never validate ungrounded token
        self.assertFalse(is_valid_name_syntax("Alarbe", "Last week, what", turn_alarbe, is_in_directory=False))
        self.assertFalse(is_valid_name_syntax("Alarbe", "what", turn_alarbe, is_in_directory=False))

        # Communicative verb with preposition validates
        turn_alex = "I talked with Alex Sanchez"
        self.assertTrue(is_valid_name_syntax("Alex", "I talked with", turn_alex, is_in_directory=False))

        # Preposition without communicative verb fails for ungrounded token
        turn_non_comm = "We worked on Fubar yesterday"
        self.assertFalse(is_valid_name_syntax("Fubar", "We worked on", turn_non_comm, is_in_directory=False))

    def test_should_surface_unconfirmed_candidate(self):
        """Tests evidence thresholding for ungrounded OOV candidate tokens."""
        # Single occurrence without relational reinforcement is rejected
        self.assertFalse(should_surface_unconfirmed_candidate("Alarbe", turn_frequency=1, has_relational_verb=False))

        # Multi-occurrence allows surfacing
        self.assertTrue(should_surface_unconfirmed_candidate("Alarbe", turn_frequency=2, has_relational_verb=False))

        # Relational reinforcement allows surfacing
        self.assertTrue(should_surface_unconfirmed_candidate("Alarbe", turn_frequency=1, has_relational_verb=True))

        # Suppressed common word always rejected
        self.assertFalse(should_surface_unconfirmed_candidate("Happened", turn_frequency=5, has_relational_verb=True))

    def test_clearance_lifecycle_quarantines_unverified_tokens(self):
        """Verifies that unverified fast-path candidate tokens are quarantined
        and excluded from meta['entity_clarifications'] under C-12 / C-27.
        """
        transcript = "Jen Pan: We had a call with Flavio on the design.\nJen Pan: Flavio confirmed the schedule."
        meta = {
            "meeting_name": "Sprint Sync",
            "attendees": ["Jen Pan"],
            "auto_resolve_recommended": True,
        }
        recorder = DummyRecorder()

        execute_clearance_lifecycle(transcript, meta, recorder)

        clarifications = meta.get("entity_clarifications", [])
        # Any ungrounded fallback without verified graph or explicit operator confirmation must be quarantined
        clarified_tokens = [c["token"].lower() for c in clarifications]
        self.assertNotIn("flavio", clarified_tokens)

        # Check quarantine ledger
        quarantined = meta.get("quarantined_entities", [])
        quarantined_tokens = [q["token"].lower() for q in quarantined]
        self.assertIn("flavio", quarantined_tokens)

    def test_clearance_lifecycle_allows_verified_graph_entities(self):
        """Verifies that verified graph entities pass through to meta['entity_clarifications']."""
        transcript = "Jen Pan: We were discussing what Ed was saying about global rules."
        meta = {
            "meeting_name": "Sprint Sync",
            "attendees": ["Jen Pan"],
            "auto_resolve_recommended": True,
        }
        recorder = DummyRecorder()

        execute_clearance_lifecycle(transcript, meta, recorder)

        clarifications = meta.get("entity_clarifications", [])
        clarified_tokens = [c["token"].lower() for c in clarifications]
        self.assertIn("ed", clarified_tokens)


if __name__ == "__main__":
    unittest.main()
