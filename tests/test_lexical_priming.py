"""
tests/test_lexical_priming.py

Unit tests for project-specific LexicalPrimer and phonetic drift normalization.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.lexical_priming import (
    LexicalPrimer,
)


class TestLexicalPriming(unittest.TestCase):
    def test_neet_lexicon_contains_energy_domain_terms(self):
        primer = LexicalPrimer("apex_trans")
        self.assertIn("ROCC", primer.lexicon)
        self.assertIn("SCADA", primer.lexicon)
        self.assertIn("EMS", primer.lexicon)
        self.assertIn("Pano AI", primer.lexicon)
        self.assertIn("NERC-CIP", primer.lexicon)
        self.assertIn("CIP-005", primer.lexicon)

    def test_build_whisper_prompt_formatting(self):
        primer = LexicalPrimer("apex_trans")
        attendees = [
            {"name": "Drew Oakley", "role": "Managing Director", "org": "PartnerAdvisory"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
            {"name": "Aaron", "role": "Participant", "org": "Unknown"},
        ]
        prompt = primer.build_whisper_prompt(
            meeting_name="APEX_TRANS Internal Discussion",
            attendees=attendees,
        )
        self.assertIn("Drew Oakley", prompt)
        self.assertIn("Amit Ayer", prompt)
        self.assertIn("ROCC", prompt)
        self.assertIn("SCADA", prompt)
        self.assertLessEqual(len(prompt), 380)

    def test_phonetic_drift_rocc_correction(self):
        primer = LexicalPrimer("apex_trans")
        # Real-world APEX_TRANS chunk 35 phonetic error
        raw_text = "So you're expecting that the people in Iraq would do this? Yeah."
        corrected = primer.post_process_transcript(raw_text)
        self.assertIn("people in the ROCC would do this", corrected)
        self.assertNotIn("people in Iraq", corrected)

    def test_phonetic_drift_scada_correction(self):
        primer = LexicalPrimer("apex_trans")
        raw_text = "And what is an example of that? It is the scater data, real-time."
        corrected = primer.post_process_transcript(raw_text)
        self.assertIn("SCADA data", corrected)

    def test_phonetic_drift_pano_correction(self):
        primer = LexicalPrimer("apex_trans")
        raw_text = "It detects smoke and fire cameras. This is pan up, yeah."
        corrected = primer.post_process_transcript(raw_text)
        self.assertIn("Pano AI", corrected)

    def test_empty_and_unrelated_text_unmodified(self):
        primer = LexicalPrimer("apex_trans")
        self.assertEqual(primer.post_process_transcript(""), "")
        clean_text = "Let's review the architectural diagram for the project."
        self.assertEqual(primer.post_process_transcript(clean_text), clean_text)


if __name__ == "__main__":
    unittest.main()
