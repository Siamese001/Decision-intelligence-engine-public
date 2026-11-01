"""
tests/test_gate1_whisper_hallucination.py

Wave 1: Unit tests for the Whisper ASR hallucination detection sub-check
added to Gate 1.4 in gate1.py.

Tests:
  1. Clean transcript — no candidates, gate passes.
  2. Hard-blocklist phrase ("Thank you.") at count=1 — flagged immediately.
  3. Hard-blocklist phrase ("The end.") at count=1 — flagged immediately.
  4. Repetitive short phrase (≥3 occurrences) — flagged as candidate.
  5. ≥5 total candidates — gate 1.4 fails (density threshold crossed).
  6. Repetitive phrase below threshold (count=2) — NOT flagged.
  7. Full run_qa_gate_1 gate still passes when candidate count < threshold.
"""

import unittest

from dual_pipeline.transcript_qa_core.gate1 import (
    _ASR_FAILURE_THRESHOLD,
    _ASR_HARD_BLOCKLIST,
    _ASR_REPETITION_THRESHOLD,
    _check_repetitive_isolated_turn_hallucinations,
)

CLEAN_TRANSCRIPT = """
## Transcript

**[00:00]** Jake Stark (PartnerAdvisory): Let's get started with the architecture review.
**[00:30]** Amit Ayer (PartnerAdvisory): I want to walk through the LENS v1.2 pipeline zones.
**[01:00]** Miles Erickson (NEA): The OCR component is still under evaluation.
**[02:00]** Jake Stark (PartnerAdvisory): Right, so the confidence threshold is configurable.
**[03:00]** Amit Ayer (PartnerAdvisory): Exactly, we have a grounded-contract window to validate the thresholds.
"""

TRANSCRIPT_WITH_THANK_YOU = """
## Transcript

**[00:00]** Jake Stark: Let's start.
Thank you.
**[00:30]** Amit Ayer: The pipeline has six zones.
Thank you.
**[01:00]** Miles Erickson: I see.
Thank you.
"""

TRANSCRIPT_WITH_THE_END = """
## Transcript

**[00:00]** Jake: Today we review the LENS pipeline.
The end.
**[00:30]** Amit: Great session.
"""

TRANSCRIPT_WITH_REPETITIVE_SHORT = """
## Transcript

**[00:00]** Jake: We discussed thresholds today.
LENS.
**[00:30]** Amit: The IOU matching was reviewed.
LENS.
**[01:00]** Miles: The OCR zone was discussed.
LENS.
**[01:30]** Jake: And the STP targets were set.
"""

TRANSCRIPT_WITH_MANY_CANDIDATES = """
## Transcript

**[00:00]** Jake: Review session.
Thank you.
The end.
LENS.
LENS.
LENS.
Jake.
Jake.
Jake.
Amit.
Amit.
Amit.
Miles.
Miles.
Miles.
**[00:30]** Amit: Continuing.
"""


class TestWhisperHallucinationDetection(unittest.TestCase):

    def test_clean_transcript_no_candidates(self):
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(CLEAN_TRANSCRIPT)
        self.assertTrue(ok, f"Expected clean transcript to pass: {failures}")
        self.assertEqual(candidates, [], f"Expected no candidates: {candidates}")
        self.assertEqual(failures, [])

    def test_hard_blocklist_thank_you_flagged_at_count_1(self):
        # Even a single occurrence of a hard-blocklisted phrase should be flagged
        transcript = "\n## Transcript\n\n**[00:00]** Jake: Hello.\nThank you.\n**[00:01]** Amit: Sure.\n"
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(transcript)
        self.assertTrue(ok, "Single hard-blocklist hit should not fail gate (below density threshold)")
        self.assertTrue(
            any("ASR_HALLUCINATION_BLOCKLIST" in c and "Thank you." in c for c in candidates),
            f"Expected 'Thank you.' to be in blocklist candidates: {candidates}",
        )

    def test_hard_blocklist_the_end_flagged(self):
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(
            TRANSCRIPT_WITH_THE_END
        )
        self.assertTrue(
            any("The end." in c and "ASR_HALLUCINATION_BLOCKLIST" in c for c in candidates),
            f"Expected 'The end.' to be flagged: {candidates}",
        )

    def test_repetitive_short_phrase_flagged_at_threshold(self):
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(
            TRANSCRIPT_WITH_REPETITIVE_SHORT
        )
        # "LENS." appears 3× — should be candidate (at repetition threshold)
        lens_flagged = any("LENS" in c for c in candidates)
        self.assertTrue(lens_flagged, f"Expected 'LENS.' to be flagged as repetitive: {candidates}")

    def test_density_threshold_fails_gate(self):
        """≥_ASR_FAILURE_THRESHOLD candidates should flip gate to FAIL."""
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(
            TRANSCRIPT_WITH_MANY_CANDIDATES
        )
        self.assertFalse(ok, "Expected gate fail with many hallucination candidates")
        self.assertTrue(len(failures) > 0, "Expected failure message for density threshold")
        self.assertIn(str(_ASR_FAILURE_THRESHOLD), failures[0])

    def test_phrase_below_repetition_threshold_not_flagged(self):
        # "Nice." appears 2× — below threshold of 3, should NOT be flagged
        transcript = (
            "\n## Transcript\n\n**[00:00]** Jake: Let's proceed.\n"
            "Nice.\n**[00:30]** Amit: Good.\nNice.\n**[01:00]** Miles: Continuing.\n"
        )
        ok, candidates, failures = _check_repetitive_isolated_turn_hallucinations(transcript)
        nice_flagged = any("Nice." in c and "CANDIDATE" in c for c in candidates)
        self.assertFalse(nice_flagged, f"'Nice.' at count=2 should not be flagged: {candidates}")

    def test_blocklist_constants_populated(self):
        """Verify hard-blocklist contains expected Whisper hallucination phrases."""
        self.assertIn("thank you.", _ASR_HARD_BLOCKLIST)
        self.assertIn("the end.", _ASR_HARD_BLOCKLIST)
        self.assertIn("bye.", _ASR_HARD_BLOCKLIST)

    def test_thresholds_sanity(self):
        self.assertGreaterEqual(_ASR_REPETITION_THRESHOLD, 3)
        self.assertGreaterEqual(_ASR_FAILURE_THRESHOLD, 5)


if __name__ == "__main__":
    unittest.main()
